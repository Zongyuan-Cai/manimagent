"""Application orchestration: checked planning, rendering, then immutable publication."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from manimagent.schemas import Answer
from manimagent.video.models import PROMPT_VERSION, VideoRequest, VideoResult, digest
from manimagent.video.planning import VideoPlanner
from manimagent.video.review import VideoReviewer


@dataclass
class RenderedVideo:
    root: Path
    final: dict
    evidence: list[dict]
    mapping: dict[str, list[dict]]


class VideoService:
    def __init__(self, store: ContentStore, provider: ChatProvider, renderer, settings: Settings):
        self.store, self.provider, self.renderer, self.settings = (
            store,
            provider,
            renderer,
            settings,
        )
        self.reviewer = VideoReviewer(store, provider, settings, self.call)

    def call(self, role, instruction, payload, schema, budget):
        cfg = self.settings.model_for(role)
        estimate = (
            len(
                (
                    instruction
                    + json.dumps(payload, ensure_ascii=False)
                    + json.dumps(schema.model_json_schema())
                ).encode("utf-8")
            )
            + 512
            + cfg.max_output_tokens
        )
        if estimate > min(cfg.context_window, self.settings.video_context_tokens):
            raise ContentError(
                "budget_exceeded",
                "动画完整上下文超过预算，请提高已验证模型的上下文配置或拆分主题",
                state="needs_input",
            )
        return self.provider.complete(
            role, instruction, payload, schema, budget, deadline=budget.deadline - 25
        )

    def generate(
        self, request: VideoRequest, budget: Budget, record: dict, progress=lambda message: None
    ) -> VideoResult:
        started = time.monotonic()
        progress("正在检查本地渲染器、中文字体与编码器")
        record["renderer_runtime"] = self.renderer.validate_runtime(
            self.store.root / "renders" / record["job_id"] / "preflight", budget
        )
        answer = self.store.load("answers", request.answer_id, Answer)
        record.update(
            video_answer_id=answer.answer_id,
            video_answer_hash=digest(answer.markdown),
            video_prompt_version=PROMPT_VERSION,
        )
        cached = {}
        if request.resume_job_id:
            previous = self.store.load_record(request.resume_job_id)
            if (
                previous.get("video_answer_id") != answer.answer_id
                or previous.get("video_answer_hash") != digest(answer.markdown)
                or previous.get("video_prompt_version") != PROMPT_VERSION
            ):
                raise ContentError("invalid_reference", "不能复用其他回答版本或不兼容提示词的分块")
            cached = previous
            record["resumed_from_job_id"] = request.resume_job_id
        self.reviewer.require_content_check(answer, budget, record)
        planner = VideoPlanner(
            self.store,
            self.call,
            self.renderer,
            self.reviewer,
            answer,
            budget,
            record,
            cached,
            progress,
        )
        plan, report = planner.build()
        self.store.save("video_plans", plan.plan_id, plan)
        record["video_plan_id"] = plan.plan_id
        self.store.save("records", record["job_id"], record)
        rendered = self.render_plan(plan, budget, record, progress)
        return self.publish_video(
            answer, plan, report, planner.repairs.used, rendered, budget, record, started
        )

    def render_plan(self, plan, budget, record, progress) -> RenderedVideo:
        plan_hash = digest(plan.model_dump_json())
        root = self.store.root / "renders" / record["job_id"]
        outputs = []
        evidence = []
        offset = 0.0
        mapping = {u.unit_id: [] for u in plan.teaching_units}
        for index, scene in enumerate(plan.scenes):
            progress(f"正在本地渲染 {index + 1}/{len(plan.scenes)}：{scene.title}")
            directory = root / scene.scene_id
            result = self.renderer.render(scene, directory, plan, budget, index)
            evidence.append(
                {
                    "scene_id": scene.scene_id,
                    "plan_hash": plan_hash,
                    "offset_seconds": offset,
                    **result,
                }
            )
            outputs.append(directory / result["video_path"])
            for event in result["events"]:
                for unit_id in event["unit_ids"]:
                    mapping[unit_id].append(
                        {
                            "scene_id": scene.scene_id,
                            "beat_id": event["beat_id"],
                            "start_seconds": round(offset + event["start_seconds"], 4),
                            "end_seconds": round(offset + event["end_seconds"], 4),
                        }
                    )
            offset += result["media"]["duration_seconds"]
            record["rendered_scenes"] = [
                {
                    "scene_id": e["scene_id"],
                    "duration_seconds": e["media"]["duration_seconds"],
                    "wall_seconds": e["wall_seconds"],
                }
                for e in evidence
            ]
            self.store.save("records", record["job_id"], record)
        progress("正在按顺序合成、完整解码并校验最终视频")
        final = self.renderer.assemble(outputs, root / "final", plan, budget)
        if any(not intervals for intervals in mapping.values()):
            raise ContentError("video_coverage", "最终视频遗漏教学单元", state="needs_review")
        return RenderedVideo(root, final, evidence, mapping)

    def publish_video(self, answer, plan, report, repairs, rendered, budget, record, started):
        root, final, evidence, mapping = (
            rendered.root,
            rendered.final,
            rendered.evidence,
            rendered.mapping,
        )
        final_duration = final["media"]["duration_seconds"]
        budget.remaining()
        asset = self.store.generated_file(
            root / "final" / final["video_path"], "teaching.mp4", "video/mp4"
        )
        report_asset = self.store.diagnostic(
            {
                "answer_id": answer.answer_id,
                "answer_hash": plan.answer_hash,
                "plan_id": plan.plan_id,
                "prompt_version": PROMPT_VERSION,
                "time_source": "estimated",
                "audio": "not_generated",
                "visual_model_review": "not_run",
                "review_mode": "trusted_template_geometry_and_layout",
                "plan_check": report.model_dump(),
                "scenes": evidence,
                "final": final,
                "repairs": repairs,
                "wall_seconds": round(time.monotonic() - started, 3),
            }
        )
        frame_mapping = []
        for scene_evidence in evidence:
            scene_id = scene_evidence["scene_id"]
            for event, frame in zip(
                scene_evidence["events"], scene_evidence["frames"], strict=True
            ):
                frame_asset = self.store.generated_file(
                    root / scene_id / frame["path"],
                    f"{scene_id}-{event['beat_id']}.png",
                    "image/png",
                )
                frame_mapping.append(
                    {
                        "scene_id": scene_id,
                        "beat_id": event["beat_id"],
                        "asset_id": frame_asset.file_id,
                        "seconds": round(
                            scene_evidence["offset_seconds"] + frame["actual_seconds"], 4
                        ),
                    }
                )
        video = VideoResult(
            video_id=new_id("video"),
            answer_id=answer.answer_id,
            answer_hash=plan.answer_hash,
            plan_id=plan.plan_id,
            video_asset_id=asset.file_id,
            duration_seconds=final_duration,
            step_mapping=[
                {
                    "step_id": s.step_id,
                    "scene_ids": [sc.scene_id for sc in plan.scenes],
                    "start_seconds": 0,
                    "end_seconds": final_duration,
                }
                for s in answer.steps
            ],
            unit_mapping=[
                {
                    "unit_id": u.unit_id,
                    "start": u.start,
                    "end": u.end,
                    "sha256": u.sha256,
                    "intervals": mapping[u.unit_id],
                }
                for u in plan.teaching_units
            ],
            report_asset_ids=[report_asset],
            frame_mapping=frame_mapping,
        )
        budget.remaining()
        self.store.save("videos", video.video_id, video)
        record["video_result"] = video.model_dump()
        record["video_elapsed_seconds"] = round(time.monotonic() - started, 3)
        return video
