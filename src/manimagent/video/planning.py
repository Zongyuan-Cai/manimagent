"""One task's outline, scene chunks and bounded revisions; no shared mutable run state."""

from __future__ import annotations

from manimagent.errors import ContentError
from manimagent.memory.projection import user_context
from manimagent.video.models import BeatDraft, SceneDraft, VideoOutline
from manimagent.video.prompts import OUTLINE_PROMPT, SCENE_PROMPT
from manimagent.video.review import RepairBudget
from manimagent.video.timeline import build_plan, resolve_scene
from manimagent.video.units import teaching_units


class VideoPlanner:
    def __init__(self, store, call, renderer, reviewer, answer, budget, record, cached, progress):
        self.store, self.call, self.renderer, self.reviewer = store, call, renderer, reviewer
        self.answer, self.budget, self.record = answer, budget, record
        self.record.setdefault("scene_chunks", {})
        self.cached, self.progress = cached, progress
        self.repairs = RepairBudget()
        self.units = teaching_units(self.answer.markdown)
        self.all_units = {u.unit_id for u in self.units}
        self.payload = {
            "profile": {"min_seconds": 120, "max_seconds": 360, "max_scenes": 8, "max_beats": 42},
            "answer_markdown": self.answer.markdown,
            "teaching_units": [u.model_dump() for u in self.units],
            "required_unit_ids": sorted(self.all_units),
        }

    def context(self, role, shared=None, **current):
        return user_context(
            {**self.payload, **(shared or {})},
            role=role,
            snapshot=self.answer.memory_snapshot,
            instruction=self.answer.instruction,
            **current,
        )

    def build_outline(self):
        self.progress("正在分析教学单元并规划整体场景")
        self.outline = (
            VideoOutline.model_validate(self.cached["video_outline"])
            if self.cached.get("video_outline")
            else self.call(
                "video_planner",
                OUTLINE_PROMPT,
                self.context("video_planner"),
                VideoOutline,
                self.budget,
            )
        )
        self.record["video_outline"] = self.outline.model_dump()
        self.store.save("records", self.record["job_id"], self.record)
        if self.outline.status != "ready":
            raise ContentError(
                "video_scope",
                self.outline.message,
                state="needs_input" if self.outline.status == "needs_input" else "needs_review",
            )
        used = {u for s in self.outline.scenes for u in s.unit_ids}
        if used != self.all_units:
            self.repairs.consume(
                ContentError("video_coverage", "整体切分遗漏正文单元", state="needs_review")
            )
            self.outline = self.call(
                "video_planner",
                OUTLINE_PROMPT,
                self.context(
                    "video_planner",
                    previous_outline=self.outline.model_dump(),
                    repair={
                        "missing_unit_ids": sorted(self.all_units - used),
                        "invalid_unit_ids": sorted(used - self.all_units),
                        "instruction": "保留完整教学安排，将每个遗漏单元分配到语义对应场景，不要漏掉任何 ID。",
                    },
                ),
                VideoOutline,
                self.budget,
            )
            self.record["video_outline"] = self.outline.model_dump()
            self.store.save("records", self.record["job_id"], self.record)
            if self.outline.status != "ready":
                raise ContentError("video_scope", self.outline.message, state="needs_review")
        if len(self.outline.scenes) < 3:
            raise ContentError("invalid_plan", "整体场景至少需要3个", state="needs_review")
        if {u for s in self.outline.scenes for u in s.unit_ids} != self.all_units or sum(
            s.beat_count for s in self.outline.scenes
        ) > 42:
            raise ContentError(
                "video_coverage", "整体切分遗漏正文单元或超出节拍上限", state="needs_review"
            )

    def chunk(self, index, feedback=None):
        spec = self.outline.scenes[index]
        self.progress(f"正在分块生成场景 {index + 1}/{len(self.outline.scenes)}：{spec.title}")
        draft = self.call(
            "scene_generator",
            SCENE_PROMPT,
            self.context(
                "scene_generator",
                shared={"global_outline": self.outline.model_dump()},
                scene=spec.model_dump(),
                scene_time_budget_seconds=round(
                    285 * spec.beat_count / sum(s.beat_count for s in self.outline.scenes)
                ),
                selected_units=[u.model_dump() for u in self.units if u.unit_id in spec.unit_ids],
                feedback=feedback,
                previous_scene=self.record["scene_chunks"].get(spec.scene_id) if feedback else None,
            ),
            SceneDraft,
            self.budget,
        )
        return resolve_scene(spec, draft)

    def layout(self, index, scene):
        self.progress(f"正在预检场景 {index + 1}/{len(self.outline.scenes)} 的公式与排版")
        return self.renderer.validate_layout(
            scene,
            self.store.root / "renders" / self.record["job_id"] / "layout" / scene.scene_id,
            self.outline,
            len(self.outline.scenes),
            self.budget,
            index,
        )

    def checked_chunk(self, index, feedback=None):
        while True:
            try:
                spec = self.outline.scenes[index]
                if feedback is None and spec.scene_id in self.cached.get("scene_chunks", {}):
                    raw = self.cached["scene_chunks"][spec.scene_id]
                    draft = SceneDraft(
                        scene_id=spec.scene_id,
                        beats=[
                            {k: v for k, v in b.items() if k in BeatDraft.model_fields}
                            for b in raw["beats"]
                        ],
                    )
                    scene = resolve_scene(spec, draft)
                else:
                    scene = self.chunk(index, feedback)
                self.record["scene_chunks"][spec.scene_id] = scene.model_dump()
                self.store.save("records", self.record["job_id"], self.record)
                self.layout(index, scene)
                return scene
            except ContentError as exc:
                if (
                    exc.error.code
                    not in {
                        "invalid_plan",
                        "video_coverage",
                        "video_pacing",
                        "render_layout",
                        "render_formula",
                    }
                    or not self.repairs.available()
                ):
                    raise
                self.repairs.consume(exc)
                feedback = (
                    exc.error.message
                    + "；保持全部必要单元，仅修正失败节拍。长公式与图共用半屏时，可将纯计算拍改为diagram.kind=none以使用整屏公式区，必要几何说明保留在其他拍。保留正确推理，不能只改标题而保留溢出布局。"
                )

    def resolve_pacing(self):
        while True:
            try:
                plan = build_plan(self.answer, self.outline, self.units, self.scenes)
                break
            except ContentError as exc:
                if exc.error.code != "video_pacing" or not self.repairs.available():
                    raise
                total = sum(s.duration_seconds for s in self.scenes)
                short = total < 120
                index = max(
                    range(len(self.scenes)),
                    key=lambda i: (
                        24 * self.scenes[i].beat_count - self.scenes[i].duration_seconds
                        if short
                        else self.scenes[i].duration_seconds
                    ),
                )
                self.repairs.consume(exc)
                feedback = {"issue": exc.error.message, "current_total_seconds": total}
                if short:
                    feedback.update(
                        required_scene_min_seconds=min(
                            23 * self.scenes[index].beat_count,
                            self.scenes[index].duration_seconds + 130 - total,
                        ),
                        instruction="展开正文中已有的条件、依据、代入与核对，让屏幕独立讲清楚；不得添加无关内容、重复结论或空等凑时长。若无法完整教学则保持真实内容，不编造。",
                    )
                else:
                    feedback.update(
                        required_scene_max_seconds=max(
                            20, self.scenes[index].duration_seconds + min(0, 340 - total)
                        ),
                        instruction="保留全部必要单元，消除重复并缩短冗余屏幕表述和旁白；不得加速、删必要推理或补空等。",
                    )
                self.scenes[index] = self.checked_chunk(index, feedback)
        return plan

    def build(self):
        self.build_outline()
        self.scenes = [self.checked_chunk(index) for index in range(len(self.outline.scenes))]
        plan = self.resolve_pacing()
        while True:
            self.progress("正在统一检查分块覆盖、数学含义与阅读节奏")
            report, revisions = self.reviewer.check_plan(
                self.answer, plan, self.budget, self.record, self.repairs
            )
            if not revisions:
                return plan, report
            scene_ids = {scene.scene_id for scene in self.scenes}
            for scene_id in revisions:
                if scene_id not in scene_ids:
                    raise ContentError(
                        "invalid_plan", "检查报告引用了不存在的场景", state="needs_review"
                    )
                index = next(i for i, scene in enumerate(self.scenes) if scene.scene_id == scene_id)
                self.repairs.consume(
                    ContentError("video_plan_check", report.message, state="needs_review")
                )
                self.scenes[index] = self.checked_chunk(index, report.model_dump())
            plan = build_plan(self.answer, self.outline, self.units, self.scenes)
