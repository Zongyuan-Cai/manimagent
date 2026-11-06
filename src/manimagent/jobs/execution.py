"""Execute one accepted job using its immutable configuration and one shared budget."""

from __future__ import annotations

import logging
import time

from manimagent.answers.service import AnswerService
from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.materials.service import MaterialService
from manimagent.memory.service import prepare_memory
from manimagent.memory.store import MemoryStore
from manimagent.schemas import (
    Answer,
    AnswerRequest,
    Error,
    Generation,
    Job,
    ParseRequest,
    Ref,
    TextGenerationRequest,
    Usage,
    now,
)
from manimagent.video.models import VideoRequest
from manimagent.video.service import VideoService

from .state import save_job

log = logging.getLogger(__name__)


class JobExecution:
    def __init__(self, store, identifier, *, stopping, transport, credentials, renderer):
        self.job = store.load("jobs", identifier, Job)
        self.record = store.load_record(identifier)
        self.settings = Settings.model_validate(self.record["settings"])
        self.store = ContentStore(
            self.settings.private_dir or self.settings.runs_dir / "private",
            self.settings.max_upload_bytes,
        )
        self.transport, self.credentials, self.renderer = transport, credentials, renderer
        self.usage = Usage()
        self.budget = Budget(self.settings, self.usage, stopping)
        self.generation = (
            self.store.load("generations", self.job.resource_ref.id, Generation)
            if self.job.resource_ref
            else None
        )
        self.stage_name = (
            "video"
            if self.job.kind == "video"
            else "answer"
            if self.job.kind == "answer"
            else "material"
        )
        self.animation_job = self.job.kind == "video" or (
            self.generation is not None and self.generation.output_mode == "both"
        )
        self.accepted = self.record.get("accepted_monotonic", self.budget.started)
        self.overall_deadline = self.accepted + min(900, self.settings.task_timeout)
        self.record["worker_started_at"] = now()
        if self.animation_job:
            self.budget.deadline = min(self.budget.deadline, self.overall_deadline)
        self.materials = MaterialService(self.store, self.settings)
        self.answers = AnswerService(self.store, self.provider_for(self.settings), self.settings)

    def save(self):
        save_job(self.store, self.job)

    def trace(self, event):
        self.record.setdefault("calls", []).append(event)
        self.store.save("records", self.job.job_id, self.record)
        if isinstance(event.get("raw_text"), str):
            self.job.diagnostic_asset_ids.append(self.store.diagnostic(event["raw_text"]))
            self.save()

    def provider_for(self, settings):
        return ChatProvider(
            settings, self.transport, trace=self.trace, credential_resolver=self.credentials
        )

    def stage(self, name):
        self.stage_name = name
        self.job.stage = name
        self.job.message = {
            "material": "正在解析材料",
            "answer": "正在生成并检查文字回答",
            "video": "正在生成教学动画",
        }[name]
        self.save()
        if self.generation:
            self.generation.stages[name].status = "running"
            self.save_generation()

    def save_generation(self):
        self.generation.updated_at = now()
        self.store.save("generations", self.generation.generation_id, self.generation)

    def progress(self, message):
        self.job.message = message
        self.save()

    def video(self, answer_id, resume_job_id=None, video_profile_id="auto"):
        self.stage("video")
        issues = self.renderer.preflight()
        if issues:
            raise ContentError("video_unavailable", "；".join(issues))
        snapshot = self.settings.model_copy(deep=True)
        for role in ("video_planner", "scene_generator", "plan_checker"):
            cfg = self.settings.model_for(role).model_copy(deep=True)
            cfg.max_output_tokens = self.settings.video_max_output_tokens
            identifier = "__video_" + role
            snapshot.models[identifier] = cfg
            snapshot.roles[role] = identifier
        snapshot.model_timeout = min(60, snapshot.model_timeout)
        snapshot.max_model_calls = min(
            self.settings.max_model_calls,
            self.usage.model_calls + self.settings.video_max_model_calls,
        )
        self.budget.settings = snapshot
        self.budget.deadline = min(
            self.overall_deadline, time.monotonic() + self.settings.video_timeout
        )
        self.budget.retry_allowance = 1
        self.record["video_started_at"] = now()
        return VideoService(
            self.store, self.provider_for(snapshot), self.renderer, snapshot
        ).generate(
            VideoRequest(
                answer_id=answer_id, resume_job_id=resume_job_id, video_profile_id=video_profile_id
            ),
            self.budget,
            self.record,
            self.progress,
        )

    def generate_outputs(self):
        request = TextGenerationRequest.model_validate(self.record["request"])
        source, generation = request.source, self.generation
        if source.type == "answer":
            answer = self.store.load("answers", source.answer_id, Answer)
            generation.material_ids = answer.material_ids
        else:
            if source.type == "inputs":
                self.stage("material")
                material = self.materials.parse(source, self.budget, self.record)
                generation.material_ids = [material.material_id]
            else:
                generation.material_ids = source.material_ids
            generation.stages["material"].status = "completed"
            self.stage("answer")
            self.prepare_memory(
                source.instruction, request.memory or getattr(source, "memory", None)
            )
            answer = self.answers.generate(
                AnswerRequest(
                    material_ids=generation.material_ids,
                    instruction=source.instruction,
                    answer_profile_id=source.answer_profile_id,
                ),
                self.budget,
                self.record,
            )
        generation.answer_id = answer.answer_id
        generation.stages["material"].status = "completed"
        generation.stages["answer"].status = "completed"
        self.save_generation()
        self.record["answer_ready_at"] = now()
        if request.output_mode == "both":
            rendered = self.video(answer.answer_id)
            generation.video_id = rendered.video_id
            generation.stages["video"].status = "completed"
            self.save_generation()
        return Ref(type="generation", id=generation.generation_id)

    def prepare_memory(self, instruction, options):
        if options is None:
            return
        self.progress("正在整理本次用户记忆并固定个性化上下文")
        snapshot, warnings = prepare_memory(
            MemoryStore(self.store.root / "memory.sqlite3"),
            self.provider_for(self.settings),
            self.budget,
            instruction,
            options,
            self.job.job_id,
        )
        self.record["memory_snapshot"] = snapshot
        self.record["memory_warnings"] = warnings
        self.store.save("records", self.job.job_id, self.record)

    def dispatch(self):
        request = self.record["request"]
        if self.job.kind == "video":
            result = self.video(
                request["answer_id"],
                request.get("resume_job_id"),
                request.get("video_profile_id", "auto"),
            )
            reference = Ref(type="video", id=result.video_id)
            self.job.resource_ref = reference
            return reference
        if self.job.kind == "parse":
            self.stage("material")
            result = self.materials.parse(
                ParseRequest.model_validate(request), self.budget, self.record
            )
            return Ref(type="material", id=result.material_id)
        if self.job.kind == "answer":
            self.stage("answer")
            parsed = AnswerRequest.model_validate(request)
            self.prepare_memory(parsed.instruction, parsed.memory)
            result = self.answers.generate(parsed, self.budget, self.record)
            return Ref(type="answer", id=result.answer_id)
        return self.generate_outputs()

    def run(self) -> Job:
        try:
            if (
                self.animation_job
                and time.monotonic() - self.accepted > self.settings.video_queue_timeout
            ):
                raise ContentError("queue_timeout", "排队超过上限，未开始长任务，请稍后重试")
            self.job.status = "running"
            self.save()
            if self.animation_job and self.job.kind != "video":
                self.budget.deadline = min(self.overall_deadline, time.monotonic() + 180)
            result = self.dispatch()
            self.job.status, self.job.stage, self.job.message, self.job.result = (
                "completed",
                "completed",
                "文字与教学动画已完成" if self.animation_job else "文字处理已完成",
                result,
            )
        except ContentError as exc:
            self.job.status, self.job.error = exc.state, exc.error
        except ProviderError as exc:
            self.job.status = "needs_review" if exc.code == "output_truncated" else "failed"
            self.job.error = Error(code=exc.code, message=str(exc), retryable=exc.retryable)
        except BudgetExceeded as exc:
            self.job.status, self.job.error = (
                "failed",
                Error(code="budget_exceeded", message=str(exc)),
            )
        except InterruptedError:
            self.job.status, self.job.error = (
                "interrupted",
                Error(code="interrupted", message="服务关闭，请重新提交", retryable=True),
            )
        except Exception:
            log.exception("任务失败 %s", self.job.job_id)
            self.job.status, self.job.error = (
                "failed",
                Error(code="internal_error", message="任务执行失败，请查看服务日志"),
            )
        finally:
            self.finish()
        return self.job

    def finish(self):
        self.usage.elapsed_seconds = round(time.monotonic() - self.budget.started, 3)
        self.job.usage = self.usage.model_dump()
        if self.job.error:
            self.job.message = self.job.error.message
            if self.generation:
                self.generation.stages[self.stage_name].status = self.job.status
                self.generation.stages[self.stage_name].error = self.job.error
                if self.stage_name == "material":
                    self.generation.stages["answer"].status = "blocked"
                if self.generation.output_mode == "both":
                    if self.generation.answer_id:
                        self.job.status = "partial"
                        self.job.result = self.job.resource_ref
                        self.job.message = "文字已保存；动画未完成：" + self.job.error.message
                    elif self.stage_name != "video":
                        self.generation.stages["video"].status = "blocked"
                self.save_generation()
        self.record["usage"] = self.job.usage
        self.record["terminal_at"] = now()
        self.store.save("records", self.job.job_id, self.record)
        diagnostic = {key: value for key, value in self.record.items() if key != "settings"}
        self.job.diagnostic_asset_ids.append(self.store.diagnostic(diagnostic))
        if self.generation and self.job.error:
            self.generation.stages[self.stage_name].diagnostic_asset_ids = list(
                self.job.diagnostic_asset_ids
            )
            self.store.save("generations", self.generation.generation_id, self.generation)
        self.save()
