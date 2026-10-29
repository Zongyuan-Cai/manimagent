from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from manimagent.agents import Agents
from manimagent.artifacts import ArtifactStore
from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.providers import ChatProvider, InvalidResponse, Provider, ProviderError
from manimagent.rendering import Renderer
from manimagent.schemas import (
    Candidate,
    GenerationRequest,
    LessonPlan,
    ReviewReport,
    RunManifest,
    SceneResult,
    SceneSpec,
)

log = logging.getLogger(__name__)


class SceneState(TypedDict, total=False):
    code: str
    candidate: Candidate


class SceneLoop:
    def __init__(
        self,
        engine: GenerationEngine,
        agents: Agents,
        manifest: RunManifest,
        plan: LessonPlan,
        scene: SceneSpec,
        result: SceneResult,
    ):
        self.engine, self.agents, self.manifest = engine, agents, manifest
        self.plan, self.scene, self.result = plan, scene, result
        self.settings, self.store, self.budget = engine.settings, engine.store, agents.budget
        self.deadline = min(self.budget.deadline, time.monotonic() + self.settings.scene_timeout)
        self.directory = self.store.job_dir(manifest.job_id) / "scenes" / scene.scene_id

    def progress(self, stage: str, message: str) -> None:
        self.engine.progress(self.manifest, stage, message)

    def run(self) -> None:
        graph = StateGraph(SceneState)
        for name in ("code", "render", "review", "repair", "revise"):
            graph.add_node(name, getattr(self, "_" + name))
        graph.add_edge(START, "code")
        graph.add_edge("code", "render")
        graph.add_conditional_edges(
            "render", self._after_render, {"repair": "repair", "review": "review", "end": END}
        )
        graph.add_edge("repair", "render")
        graph.add_conditional_edges("review", self._after_review, {"revise": "revise", "end": END})
        graph.add_edge("revise", "render")
        try:
            graph.compile().invoke({}, {"recursion_limit": 60})
        except (BudgetExceeded, ProviderError) as exc:
            self.result.message = str(exc)
        eligible = [
            c for c in self.result.candidates if c.review and c.review.qualifies(self.scene)
        ]
        if eligible:
            selected = max(eligible, key=lambda c: c.review.scores.mean)
            self.result.selected_attempt_id = selected.attempt_id
            self._formal(selected)
        else:
            self.result.status = (
                "needs_review"
                if any(c.video_artifact_id for c in self.result.candidates)
                else "failed"
            )
            self.result.message = self.result.message or "未取得通过检查的场景候选"
        self.store.save(self.manifest)

    def _code(self, state: SceneState) -> dict:
        self.progress("coding", f"正在生成场景 {self.scene.scene_id}")
        return {"code": self.agents.code(self.plan, self.scene, self.deadline)}

    def _render(self, state: SceneState) -> dict:
        remaining = self.budget.remaining(self.deadline)
        self.progress("rendering", f"正在渲染场景 {self.scene.scene_id}")
        attempt_id = f"candidate_{len(self.result.candidates):02d}"
        directory = self.directory / attempt_id
        directory.mkdir(parents=True, exist_ok=True)
        # Canonical code lives outside the directory mounted into the container.
        source = directory / "code.py"
        source.write_text(state["code"], encoding="utf-8")
        code_id = self.store.register(
            self.manifest, source, "code", f"{self.scene.scene_id} {attempt_id} 代码"
        )
        rr = self.engine.renderer.render(
            state["code"],
            self.scene.class_name,
            directory / "work",
            False,
            min(remaining, self.settings.render_timeout),
        )
        candidate = Candidate(
            attempt_id=attempt_id,
            scene_id=self.scene.scene_id,
            plan_revision=self.manifest.plan_revision,
            code_artifact_id=code_id,
            render=rr,
        )
        if rr.status == "success" and rr.video_path:
            candidate.video_artifact_id = self.store.register(
                self.manifest,
                Path(rr.video_path),
                "preview",
                f"{self.scene.scene_id} {attempt_id} 预览",
            )
        self.result.candidates.append(candidate)
        self.store.write_json(directory / "render_result.json", rr.model_dump())
        self.store.save(self.manifest)
        return {"candidate": candidate}

    def _after_render(self, state: SceneState) -> str:
        candidate = state["candidate"]
        if candidate.render.status == "success":
            return "review"
        self.result.message = candidate.render.message[:600]
        if (
            candidate.render.error_category != "environment"
            and self.result.execution_repairs < self.settings.max_execution_repairs
            and len(self.result.candidates) < self.settings.max_candidates
        ):
            return "repair"
        return "end"

    def _repair(self, state: SceneState) -> dict:
        self.result.execution_repairs += 1
        self.progress("coding", f"正在修复场景 {self.scene.scene_id} 的执行问题")
        return {
            "code": self.agents.code(
                self.plan,
                self.scene,
                self.deadline,
                state["code"],
                state["candidate"].render.model_dump(),
                "execution_repair",
            )
        }

    def _review(self, state: SceneState) -> dict:
        candidate = state["candidate"]
        self.progress("reviewing", f"正在检查场景 {self.scene.scene_id} 的画面")
        candidate.review = self.engine.review(
            self.agents, self.plan, self.scene, candidate.render, self.deadline
        )
        directory = self.directory / candidate.attempt_id
        self.store.write_json(directory / "review.json", candidate.review.model_dump())
        self.store.register(
            self.manifest,
            directory / "review.json",
            "report",
            f"{self.scene.scene_id} {candidate.attempt_id} 检查",
        )
        self.store.save(self.manifest)
        return {"candidate": candidate}

    def _after_review(self, state: SceneState) -> str:
        review = state["candidate"].review
        if review.qualifies(self.scene):
            return "end"
        self.result.message = review.message or "场景尚未通过视觉检查"
        if (
            review.review_status == "valid"
            and review.decision != "fail"
            and not review.requires_replanning
            and self.result.visual_revisions < self.settings.max_visual_revisions
            and len(self.result.candidates) < self.settings.max_candidates
        ):
            return "revise"
        return "end"

    def _revise(self, state: SceneState) -> dict:
        self.result.visual_revisions += 1
        self.progress("coding", f"正在修订场景 {self.scene.scene_id} 的画面")
        return {
            "code": self.agents.code(
                self.plan,
                self.scene,
                self.deadline,
                state["code"],
                state["candidate"].review.model_dump(),
                "visual_reviser",
            )
        }

    def _formal(self, selected: Candidate) -> None:
        self.progress("finalizing", f"正在正式渲染场景 {self.scene.scene_id}")
        code = self.store.resolve(self.manifest, selected.code_artifact_id).read_text(
            encoding="utf-8"
        )
        directory = self.directory / "formal"
        rr = self.engine.renderer.render(
            code,
            self.scene.class_name,
            directory / "work",
            True,
            min(self.budget.remaining(), self.settings.render_timeout),
        )
        self.store.write_json(directory / "render_result.json", rr.model_dump())
        if rr.status != "success" or not rr.video_path:
            self.result.status = "needs_review"
            self.result.message = "预览通过，但正式渲染未成功：" + rr.message[:300]
            return
        artifact_id = self.store.register(
            self.manifest,
            Path(rr.video_path),
            "preview",
            f"{self.scene.scene_id} 正式候选（待检查）",
        )
        review = self.engine.review(self.agents, self.plan, self.scene, rr)
        self.result.formal_review = review
        self.store.write_json(directory / "review.json", review.model_dump())
        self.store.register(
            self.manifest, directory / "review.json", "report", f"{self.scene.scene_id} 正式检查"
        )
        if review.qualifies(self.scene):
            self.result.formal_video_artifact_id = artifact_id
            self.manifest.artifacts[artifact_id].kind = "scene_video"
            self.manifest.artifacts[artifact_id].label = f"{self.scene.scene_id} 正式场景"
            self.result.status = "completed"
            self.result.message = "场景已通过预览和正式检查"
        else:
            self.result.status = "needs_review"
            self.result.message = "正式画面未通过检查，已保留预览与正式候选"


class GenerationEngine:
    def __init__(
        self, settings: Settings, store: ArtifactStore, provider: Provider, renderer: Renderer
    ):
        self.settings, self.store, self.provider, self.renderer = (
            settings,
            store,
            provider,
            renderer,
        )
        self.stopping = threading.Event()

    def readiness(self) -> list[str]:
        errors = (
            self.settings.configuration_errors() if isinstance(self.provider, ChatProvider) else []
        )
        return errors + self.renderer.preflight()

    def progress(self, manifest: RunManifest, stage: str, message: str) -> None:
        if self.stopping.is_set():
            raise InterruptedError("服务已停止，任务中断")
        manifest.stage, manifest.message = stage, message
        self.store.save(manifest)

    def review(
        self, agents: Agents, plan: LessonPlan, scene: SceneSpec, rr, deadline: float | None = None
    ) -> ReviewReport:
        try:
            if len(rr.frames) != 4 or len(rr.timestamps) != 4:
                raise InvalidResponse("没有完整的四帧检查输入")
            return agents.review(plan, scene, [Path(p) for p in rr.frames], rr.timestamps, deadline)
        except ProviderError as exc:
            return ReviewReport(
                review_status="invalid" if isinstance(exc, InvalidResponse) else "unavailable",
                decision="fail",
                message=str(exc),
            )

    def execute(self, job_id: str) -> RunManifest:
        manifest = self.store.load(job_id)
        if manifest.status != "queued":
            return manifest
        budget = Budget(self.settings, manifest.usage, self.stopping)
        agents = Agents(self.provider, budget)
        manifest.status = "running"
        try:
            budget.remaining()
            errors = self.readiness()
            if errors:
                raise ProviderError("；".join(errors))
            request = GenerationRequest.model_validate_json(
                (self.store.job_dir(job_id) / "request.json").read_text(encoding="utf-8")
            )
            snapshot = self.settings.model_dump(mode="json", exclude={"runs_dir"})
            self.store.write_json(self.store.job_dir(job_id) / "configuration.json", snapshot)
            if re.fullmatch(r"https?://\S+", request.text):
                manifest.status, manifest.message = (
                    "needs_input",
                    "请直接粘贴学习内容，MVP 不抓取网页链接",
                )
                return manifest
            self.progress(manifest, "planning", "正在理解文本并规划讲解")
            feedback = ""
            plan = None
            for revision in range(2):
                response = agents.plan(request.text, feedback)
                if response.status == "needs_input":
                    manifest.status, manifest.message = "needs_input", response.message
                    return manifest
                plan = response.plan
                if len(plan.scenes) > self.settings.max_scenes:
                    feedback = "场景数量超出应用配置限制"
                    continue
                check = agents.check_plan(request.text, plan)
                if check.decision == "needs_input":
                    manifest.status, manifest.message = (
                        "needs_input",
                        check.message or "请补充必要题设",
                    )
                    return manifest
                if check.decision == "pass":
                    manifest.plan_revision = revision + 1
                    break
                feedback = check.message
            else:
                raise ProviderError("规划修正后仍未通过检查：" + feedback[:300])
            manifest.title = plan.title
            manifest.total_scenes = len(plan.scenes)
            manifest.scene_results = [SceneResult(scene_id=s.scene_id) for s in plan.scenes]
            path = self.store.job_dir(job_id) / "lesson_plan.json"
            self.store.write_json(path, plan.model_dump())
            self.store.register(manifest, path, "plan", "讲解规划")
            for scene, result in zip(plan.scenes, manifest.scene_results, strict=True):
                budget.remaining()
                SceneLoop(self, agents, manifest, plan, scene, result).run()
            if any(s.status != "completed" for s in manifest.scene_results):
                self._incomplete(manifest, "部分场景未通过检查，请查看场景预览和报告")
                return manifest
            self.progress(manifest, "assembling", "正在合成并验证完整视频")
            videos = [
                self.store.resolve(manifest, s.formal_video_artifact_id)
                for s in manifest.scene_results
            ]
            assembly = self.renderer.assemble(
                videos,
                self.store.job_dir(job_id) / "assembly",
                min(budget.remaining(), self.settings.render_timeout),
            )
            if assembly.status != "success" or not assembly.video_path:
                self._incomplete(manifest, "视频合成或验证失败：" + assembly.message[:300])
                return manifest
            budget.remaining()
            final = self.store.job_dir(job_id) / "final.mp4"
            shutil.copyfile(assembly.video_path, final)
            manifest.final_artifact_id = self.store.register(
                manifest, final, "final", "完整讲解视频"
            )
            manifest.status, manifest.message = "completed", "动画已生成并完成规定检查"
        except InterruptedError:
            manifest.status, manifest.message = "interrupted", "服务已停止，已有产物已保留"
        except (BudgetExceeded, ProviderError) as exc:
            self._incomplete(manifest, str(exc))
        except Exception:
            log.exception("Job %s failed", job_id)
            self._incomplete(manifest, "生成发生内部错误，请查看服务日志；已有产物已保留")
        finally:
            manifest.usage.elapsed_seconds = round(time.monotonic() - budget.started, 3)
            self.store.save(manifest)
        return manifest

    @staticmethod
    def _incomplete(manifest: RunManifest, message: str) -> None:
        manifest.status = (
            "needs_review"
            if any(a.kind in {"preview", "scene_video"} for a in manifest.artifacts.values())
            else "failed"
        )
        manifest.message = message

    def stop(self) -> None:
        self.stopping.set()
        close = getattr(self.renderer, "close", None)
        if close:
            close()
