from __future__ import annotations

import logging
import queue
import threading
import time

from manimagent.animation import VideoService
from manimagent.animation_models import VideoRequest
from manimagent.animation_renderer import NativeRenderer
from manimagent.answers import AnswerService
from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.content_store import ContentError, ContentStore, new_id
from manimagent.llm_settings import LLMSettingsStore
from manimagent.materials import MaterialService
from manimagent.providers import ChatProvider, ProviderError
from manimagent.schemas import (
    Answer,
    AnswerRequest,
    Error,
    Generation,
    Job,
    MaterialIR,
    ParseRequest,
    Ref,
    StageState,
    TextGenerationRequest,
    Usage,
    now,
)

log = logging.getLogger(__name__)


class TextJobManager:
    """Single-user, single-process persistent jobs; only the waiting queue is in memory."""

    def __init__(self, settings: Settings, provider: ChatProvider | None = None, renderer=None):
        self.settings = settings
        self.store = ContentStore(settings)
        self.llm_settings = LLMSettingsStore(settings, self.store.root)
        self.settings = self.llm_settings.effective(settings)
        self.provider = provider
        self.renderer = renderer or NativeRenderer()
        self.queue: queue.Queue[str] = queue.Queue()
        self.slots = threading.BoundedSemaphore(settings.max_waiting + 1)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self._work, name="manimagent-text-worker", daemon=True
        )

    def save_job(self, job: Job) -> None:
        job.updated_at = now()
        self.store.save("jobs", job.job_id, job)

    def restore_published_answer(self, job: Job) -> bool:
        if not job.resource_ref or job.resource_ref.type != "generation":
            return False
        generation = self.store.load("generations", job.resource_ref.id, Generation)
        if not generation.answer_id:
            return False
        self.store.load("answers", generation.answer_id, Answer)
        if generation.output_mode == "both" and not generation.video_id:
            job.status, job.message = "partial", "文字已保存；动画中断，可用同一回答重新生成视频"
            generation.stages["video"].status = "interrupted"
            generation.stages["video"].error = job.error
            self.store.save("generations", generation.generation_id, generation)
        else:
            job.status, job.message, job.error = "completed", "结果已保存", None
        job.result = job.resource_ref
        return True

    def interrupt(self, job: Job, message: str) -> None:
        job.status, job.message = "interrupted", message
        job.error = Error(code="interrupted", message=message, retryable=True)
        if not self.restore_published_answer(job) and job.resource_ref:
            generation = self.store.load("generations", job.resource_ref.id, Generation)
            for stage in generation.stages.values():
                if stage.status in {"running", "pending"}:
                    stage.status, stage.error = "interrupted", job.error
            generation.updated_at = now()
            self.store.save("generations", generation.generation_id, generation)
        self.save_job(job)

    def start(self) -> None:
        for path in (self.store.root / "jobs").glob("*.json"):
            job = Job.model_validate_json(path.read_bytes())
            if job.status in {"queued", "running"}:
                self.interrupt(job, "服务已重启，请重新提交；已保存结果仍可读取")
        self.thread.start()

    def readiness(self, settings: Settings | None = None) -> list[str]:
        cfg = settings or self.settings
        roles = ("answer_writer", "answer_checker") if cfg.answer_check else ("answer_writer",)
        return cfg.configuration_errors(roles, self.llm_settings.resolve)

    def validate(self, kind: str, request, settings: Settings) -> None:
        service = MaterialService(self.store)
        needs_answer = kind == "answer"
        if kind == "video":
            self.store.load("answers", request.answer_id, Answer)
            errors = self.renderer.preflight() + settings.configuration_errors(
                ("video_planner", "scene_generator", "plan_checker"), self.llm_settings.resolve
            )
            if errors:
                raise ContentError("video_unavailable", "；".join(errors), 503)
        elif kind == "parse":
            service.validate(request)
        elif kind == "generation":
            source = request.source
            needs_answer = source.type != "answer"
            if source.type == "inputs":
                service.validate(
                    ParseRequest.model_validate(
                        source.model_dump(include={"inputs", "parser_profile_id", "input_mode"})
                    )
                )
            elif source.type == "answer":
                self.store.load("answers", source.answer_id, Answer)
        source = request.source if kind == "generation" else request
        if hasattr(source, "material_ids"):
            for identifier in source.material_ids:
                self.store.load("materials", identifier, MaterialIR)
        if needs_answer:
            errors = self.readiness(settings)
            if errors:
                raise ContentError("model_unavailable", "；".join(errors), 503)

    def submit(
        self,
        kind: str,
        request: ParseRequest | AnswerRequest | TextGenerationRequest | VideoRequest,
    ) -> Job:
        with self.lock:
            snapshot = self.settings.model_copy(deep=True)
            self.validate(kind, request, snapshot)
            if self.stopping.is_set() or not self.slots.acquire(blocking=False):
                raise ContentError("queue_full", "文字任务队列已满，请稍后重试", 429)
            job = Job(job_id=new_id("job"), kind=kind)
            generation = None
            try:
                if kind == "generation":
                    generation = Generation(
                        generation_id=new_id("generation"),
                        source=request.source.model_dump(),
                        active_job_id=job.job_id,
                        job_ids=[job.job_id],
                        output_mode=request.output_mode,
                    )
                    if request.output_mode == "both":
                        generation.stages["video"] = StageState()
                    job.resource_ref = Ref(type="generation", id=generation.generation_id)
                    self.store.save("generations", generation.generation_id, generation)
                self.store.save(
                    "records",
                    job.job_id,
                    {
                        "job_id": job.job_id,
                        "request": request.model_dump(),
                        "settings": snapshot.model_dump(mode="json"),
                        "accepted_monotonic": time.monotonic(),
                        "accepted_at": job.created_at,
                    },
                )
                self.save_job(job)
                self.queue.put_nowait(job.job_id)
            except Exception:
                self.store.delete_unpublished("jobs", job.job_id)
                self.store.delete_unpublished("records", job.job_id)
                if generation:
                    self.store.delete_unpublished("generations", generation.generation_id)
                self.slots.release()
                raise
            return job

    def _work(self) -> None:
        while not self.stopping.is_set():
            try:
                identifier = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.execute(identifier)
            except Exception:
                log.exception("文字 Worker 无法保存任务 %s", identifier)
                # If a one-off write failed, still try to publish a terminal failure.
                # A persistent disk failure remains visible in the server log and is
                # reconciled as interrupted when storage becomes available on restart.
                try:
                    job = self.store.load("jobs", identifier, Job)
                    if not self.restore_published_answer(job):
                        job.status = "failed"
                        job.error = Error(
                            code="storage_error", message="任务记录保存失败，请检查存储后重新提交"
                        )
                        job.message = job.error.message
                        job.result = None
                    self.save_job(job)
                except Exception:
                    log.exception("无法写入失败状态 %s", identifier)
            finally:
                self.queue.task_done()
                self.slots.release()

    def execute(self, identifier: str) -> Job:
        job = self.store.load("jobs", identifier, Job)
        record = self.store.load_record(identifier)
        settings = Settings.model_validate(record["settings"])
        store = ContentStore(settings)
        usage = Usage()
        budget = Budget(settings, usage, self.stopping)
        generation = (
            store.load("generations", job.resource_ref.id, Generation) if job.resource_ref else None
        )
        stage_name = (
            "video" if job.kind == "video" else "material" if job.kind != "answer" else "answer"
        )
        animation_job = job.kind == "video" or (
            generation is not None and generation.output_mode == "both"
        )
        accepted = record.get("accepted_monotonic", budget.started)
        overall_deadline = accepted + min(900, settings.task_timeout)
        record["worker_started_at"] = now()
        if animation_job:
            budget.deadline = min(budget.deadline, overall_deadline)

        def trace(event):
            record.setdefault("calls", []).append(event)
            store.save("records", job.job_id, record)
            if isinstance(event.get("raw_text"), str):
                job.diagnostic_asset_ids.append(store.diagnostic(event["raw_text"]))
                self.save_job(job)

        provider = ChatProvider(
            settings,
            self.provider.transport if self.provider else None,
            trace=trace,
            credential_resolver=self.llm_settings.resolve,
        )
        materials, answers = MaterialService(store), AnswerService(store, provider)

        def stage(name: str):
            nonlocal stage_name
            stage_name = name
            job.stage, job.message = (
                name,
                "正在解析材料"
                if name == "material"
                else "正在生成并检查文字回答"
                if name == "answer"
                else "正在生成无声动画",
            )
            self.save_job(job)
            if generation:
                generation.stages[name].status = "running"
                generation.updated_at = now()
                store.save("generations", generation.generation_id, generation)

        def video(answer_id, resume_job_id=None):
            stage("video")
            issues = self.renderer.preflight()
            if issues:
                raise ContentError("video_unavailable", "；".join(issues))
            snapshot = settings.model_copy(deep=True)
            for role in ("video_planner", "scene_generator", "plan_checker"):
                cfg = settings.model_for(role).model_copy(deep=True)
                cfg.max_output_tokens = settings.video_max_output_tokens
                identifier = "__video_" + role
                snapshot.models[identifier] = cfg
                snapshot.roles[role] = identifier
            snapshot.model_timeout = min(60, snapshot.model_timeout)
            snapshot.max_model_calls = min(
                settings.max_model_calls, usage.model_calls + settings.video_max_model_calls
            )
            budget.settings = snapshot
            budget.deadline = min(overall_deadline, time.monotonic() + settings.video_timeout)
            budget.retry_allowance = 1
            record["video_started_at"] = now()

            def progress(message):
                job.message = message
                self.save_job(job)

            video_provider = ChatProvider(
                snapshot,
                self.provider.transport if self.provider else None,
                trace=trace,
                credential_resolver=self.llm_settings.resolve,
            )
            return VideoService(store, video_provider, self.renderer).generate(
                VideoRequest(answer_id=answer_id, resume_job_id=resume_job_id),
                budget,
                record,
                progress,
            )

        try:
            if animation_job and time.monotonic() - accepted > settings.video_queue_timeout:
                raise ContentError("queue_timeout", "排队超过上限，未开始长任务，请稍后重试")
            job.status = "running"
            self.save_job(job)
            if animation_job and job.kind != "video":
                budget.deadline = min(overall_deadline, time.monotonic() + 180)
            if job.kind == "video":
                result = video(
                    record["request"]["answer_id"], record["request"].get("resume_job_id")
                )
                result_ref = Ref(type="video", id=result.video_id)
                job.resource_ref = result_ref
            elif job.kind == "parse":
                stage("material")
                result = materials.parse(
                    ParseRequest.model_validate(record["request"]), budget, record
                )
                result_ref = Ref(type="material", id=result.material_id)
            elif job.kind == "answer":
                stage("answer")
                result = answers.generate(
                    AnswerRequest.model_validate(record["request"]),
                    budget,
                    record,
                )
                result_ref = Ref(type="answer", id=result.answer_id)
            else:
                request = TextGenerationRequest.model_validate(record["request"])
                source = request.source
                if source.type == "answer":
                    result = store.load("answers", source.answer_id, Answer)
                    generation.material_ids = result.material_ids
                else:
                    if source.type == "inputs":
                        stage("material")
                        material = materials.parse(
                            ParseRequest.model_validate(
                                source.model_dump(
                                    include={"inputs", "parser_profile_id", "input_mode"}
                                )
                            ),
                            budget,
                            record,
                        )
                        generation.material_ids = [material.material_id]
                    else:
                        generation.material_ids = source.material_ids
                    generation.stages["material"].status = "completed"
                    stage("answer")
                    result = answers.generate(
                        AnswerRequest(
                            material_ids=generation.material_ids,
                            instruction=source.instruction,
                            answer_profile_id=source.answer_profile_id,
                        ),
                        budget,
                        record,
                    )
                generation.answer_id = result.answer_id
                generation.stages["material"].status = "completed"
                generation.stages["answer"].status = "completed"
                generation.updated_at = now()
                store.save("generations", generation.generation_id, generation)
                record["answer_ready_at"] = now()
                if request.output_mode == "both":
                    rendered = video(result.answer_id)
                    generation.video_id = rendered.video_id
                    generation.stages["video"].status = "completed"
                    generation.updated_at = now()
                    store.save("generations", generation.generation_id, generation)
                result_ref = Ref(type="generation", id=generation.generation_id)
            job.status, job.stage, job.message, job.result = (
                "completed",
                "completed",
                "文字与无声动画已完成" if animation_job else "文字处理已完成",
                result_ref,
            )
        except ContentError as exc:
            job.status, job.error = exc.state, exc.error
        except ProviderError as exc:
            job.status = "needs_review" if exc.code == "output_truncated" else "failed"
            job.error = Error(code=exc.code, message=str(exc), retryable=exc.retryable)
        except BudgetExceeded as exc:
            job.status, job.error = "failed", Error(code="budget_exceeded", message=str(exc))
        except InterruptedError:
            job.status, job.error = (
                "interrupted",
                Error(code="interrupted", message="服务关闭，请重新提交", retryable=True),
            )
        except Exception:
            log.exception("文字任务失败 %s", identifier)
            job.status, job.error = (
                "failed",
                Error(code="internal_error", message="文字任务执行失败，请查看服务日志"),
            )
        finally:
            usage.elapsed_seconds = round(time.monotonic() - budget.started, 3)
            job.usage = usage.model_dump()
            if job.error:
                job.message = job.error.message
                if generation:
                    generation.stages[stage_name].status = job.status
                    generation.stages[stage_name].error = job.error
                    if stage_name == "material":
                        generation.stages["answer"].status = "blocked"
                    if generation.output_mode == "both":
                        if generation.answer_id:
                            job.status = "partial"
                            job.result = job.resource_ref
                            job.message = "文字已保存；动画未完成：" + job.error.message
                        elif stage_name != "video":
                            generation.stages["video"].status = "blocked"
                    generation.updated_at = now()
                    store.save("generations", generation.generation_id, generation)
            # The diagnostic copy excludes absolute storage paths and private model settings.
            record["usage"] = job.usage
            record["terminal_at"] = now()
            store.save("records", job.job_id, record)
            diagnostic = {key: value for key, value in record.items() if key != "settings"}
            job.diagnostic_asset_ids.append(store.diagnostic(diagnostic))
            if generation and job.error:
                generation.stages[stage_name].diagnostic_asset_ids = list(job.diagnostic_asset_ids)
                store.save("generations", generation.generation_id, generation)
            self.save_job(job)
        return job

    def stop(self) -> None:
        with self.lock:
            self.stopping.set()
        while True:
            try:
                identifier = self.queue.get_nowait()
            except queue.Empty:
                break
            job = self.store.load("jobs", identifier, Job)
            self.interrupt(job, "服务关闭，请重新提交")
            self.queue.task_done()
            self.slots.release()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
