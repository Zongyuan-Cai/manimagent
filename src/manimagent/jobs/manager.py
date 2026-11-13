"""Accept, queue and own jobs; execution policy lives in execution.py."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time

from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.instance import InstanceLease
from manimagent.llm.client import ChatProvider
from manimagent.llm.settings import LLMSettingsStore
from manimagent.materials.service import MaterialService
from manimagent.memory.audit import publish_memory_context
from manimagent.memory.learning import LearningStore
from manimagent.memory.store import MemoryStore
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
)
from manimagent.video.models import VideoRequest
from manimagent.video.renderer.process import NativeRenderer
from manimagent.video.settings import TTSSettingsStore

from .execution import JobExecution
from .learning import MemoryLearningWorker
from .state import interrupt, restore_published_answer, save_job

log = logging.getLogger(__name__)


class JobManager:
    """Single-user process lease and bounded queue; accepted tasks own immutable settings."""

    def __init__(self, settings: Settings, provider: ChatProvider | None = None, renderer=None):
        self.store = ContentStore(
            settings.private_dir or settings.runs_dir / "private", settings.max_upload_bytes
        )
        self.instance = InstanceLease(self.store.root)
        self.llm_settings = LLMSettingsStore(settings, self.store.root)
        self.tts_settings = TTSSettingsStore(self.store.root, self.llm_settings.credentials)
        self.settings = self.tts_settings.effective(self.llm_settings.effective(settings))
        self.provider = provider
        self.memory = MemoryStore(self.store.root / "memory.sqlite3", self.settings.memory_policy)
        self.learning = LearningStore(self.memory.path)
        self.memory_slots = threading.BoundedSemaphore(1)
        self.renderer = renderer or NativeRenderer()
        self.queue: queue.Queue[str] = queue.Queue()
        self.slots = threading.BoundedSemaphore(settings.max_waiting + 1)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.learning_worker = MemoryLearningWorker(self)
        self.learning_thread = threading.Thread(
            target=self.learning_worker.run, name="manimagent-memory", daemon=True
        )
        self.thread = threading.Thread(target=self._work, name="manimagent-worker", daemon=True)

    def start(self) -> None:
        if self.thread.is_alive():
            return
        self.instance.acquire()
        try:
            for path in (self.store.root / "jobs").glob("*.json"):
                job = Job.model_validate_json(path.read_bytes())
                if job.status in {"queued", "running"}:
                    interrupt(self.store, job, "服务已重启，请重新提交；已保存结果仍可读取")
            self.learning.recover()
            # Recover the crash window between the durable interpretation and
            # queue insertion, without running the interpreter again.
            from manimagent.personalization import Interpretation

            for path in (self.store.root / "records").glob("*.json"):
                record = self.store.load_record(path.stem)
                if record.get("memory_save", {}).get("state") == "pending":
                    interpretation = Interpretation.model_validate(record["interpretation"])
                    self.enqueue_memory(record["job_id"], interpretation, record)
                    self.store.save("records", record["job_id"], record)
                    recovered_job = self.store.load("jobs", record["job_id"], Job)
                    recovered_job.memory_save = dict(record["memory_save"])
                    save_job(self.store, recovered_job)
            self.memory.cleanup()
            self.learning.cleanup()
            self.learning_thread.start()
            self.thread.start()
        except BaseException:
            self.stopping.set()
            self.learning_worker.wake.set()
            if self.learning_thread.is_alive():
                self.learning_thread.join()
            self.instance.release()
            raise

    def readiness(self, settings: Settings | None = None) -> list[str]:
        cfg = settings or self.settings
        roles = ("answer_writer", "answer_checker") if cfg.answer_check else ("answer_writer",)
        return cfg.configuration_errors(roles, self.llm_settings.credentials.resolve)

    def validate(self, kind: str, request, settings: Settings) -> None:
        service = MaterialService(self.store, settings)
        wants_video = kind == "video" or (kind == "generation" and request.output_mode == "both")
        profile = getattr(request, "video_profile_id", "auto")
        if wants_video and (
            profile == "standard_narrated" or (profile == "auto" and settings.tts.enabled)
        ):
            speech_status = self.tts_settings.status(settings)
            if error := speech_status.get("credential_error"):
                raise ContentError(error["code"], error["message"], 503)
            if not speech_status["ready"]:
                raise ContentError(
                    "tts_not_configured",
                    "配音尚未配置，请在语音设置中填写 API Key 并试听，或选择静音动画／仅完整解答",
                    422,
                )
        needs_answer = kind == "answer"
        if kind == "video":
            self.store.load("answers", request.answer_id, Answer)
            errors = self.renderer.preflight() + settings.configuration_errors(
                ("video_planner", "scene_generator", "plan_checker"),
                self.llm_settings.credentials.resolve,
            )
            if errors:
                raise ContentError("video_unavailable", "；".join(errors), 503)
        elif kind == "parse":
            service.validate(request)
        elif kind == "generation":
            source = request.source
            needs_answer = source.type != "answer"
            if source.type == "inputs":
                service.validate(source)
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
                raise ContentError("queue_full", "任务队列已满，请稍后重试", 429)
            job = Job(
                job_id=new_id("job"),
                kind=kind,
                answer_id=request.answer_id if kind == "video" else None,
            )
            generation = None
            try:
                if kind == "generation":
                    generation = Generation(
                        generation_id=new_id("generation"),
                        source=request.source.model_dump(),
                        active_job_id=job.job_id,
                        job_ids=[job.job_id],
                        output_mode=request.output_mode,
                        video_profile_id=request.video_profile_id,
                    )
                    if request.output_mode == "both":
                        generation.stages["video"] = StageState()
                    job.resource_ref = Ref(type="generation", id=generation.generation_id)
                    self.store.save("generations", generation.generation_id, generation)
                record = {
                    "pipeline_version": "fixed-2",
                    "job_id": job.job_id,
                    "request": request.model_dump(),
                    "settings": snapshot.model_dump(mode="json"),
                    "accepted_monotonic": time.monotonic(),
                    "accepted_at": job.created_at,
                    "memory_snapshot": None,
                }
                source = request.source if kind == "generation" else request
                options = getattr(request, "memory", None) or getattr(source, "memory", None)
                if options is not None:
                    accepted = self.memory.accept(
                        source_id=job.job_id,
                        session_id=options.session_id,
                        source_text=source.instruction,
                        scope=options.scope,
                        read=options.read,
                    )
                    record["memory_snapshot"] = accepted["snapshot"]
                    record["memory_acceptance"] = {
                        "sequence": accepted["sequence"],
                        "options": options.model_dump(),
                    }
                elif kind == "video" or (kind == "generation" and request.source.type == "answer"):
                    answer = self.store.load("answers", source.answer_id, Answer)
                    record["memory_snapshot"] = answer.memory_snapshot
                    record["memory_snapshot_known"] = "memory_snapshot" in answer.model_fields_set
                    record["memory_snapshot_source"] = {"answer_id": answer.answer_id}
                self.store.save("records", job.job_id, record)
                save_job(self.store, job)
                publish_memory_context(self.store.root, record, status=job.status)
                if record.get("memory_context_export_error"):
                    self.store.save("records", job.job_id, record)
                self.queue.put_nowait(job.job_id)
            except Exception:
                try:
                    unpublished = [("jobs", job.job_id), ("records", job.job_id)]
                    if generation:
                        unpublished.append(("generations", generation.generation_id))
                    for collection, identifier in unpublished:
                        try:
                            self.store.delete_unpublished(collection, identifier)
                        except Exception:
                            log.warning("未能清理未发布资源 %s/%s", collection, identifier)
                finally:
                    self.slots.release()
                raise
            return job

    def _work(self) -> None:
        try:
            self._work_until_stopped()
        finally:
            self.stopping.set()
            self.learning_worker.wake.set()
            # A late HTTP response must finish or abort before another process can
            # take ownership of this memory database, even after stop() times out.
            self.learning_thread.join()
            self.instance.release()

    def _work_until_stopped(self) -> None:
        while not self.stopping.is_set():
            try:
                identifier = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.execute(identifier)
            except Exception:
                log.exception("任务 Worker 无法保存任务 %s", identifier)
                # If a one-off write failed, still try to publish a terminal failure.
                # A persistent disk failure remains visible in the server log and is
                # reconciled as interrupted when storage becomes available on restart.
                try:
                    job = self.store.load("jobs", identifier, Job)
                    if not restore_published_answer(self.store, job):
                        job.status = "failed"
                        job.error = Error(
                            code="storage_error", message="任务记录保存失败，请检查存储后重新提交"
                        )
                        job.message = job.error.message
                        job.result = None
                    save_job(self.store, job)
                except Exception:
                    log.exception("无法写入失败状态 %s", identifier)
            finally:
                self.queue.task_done()
                self.slots.release()
                self.learning_worker.wake.set()

    def stop(self) -> None:
        with self.lock:
            self.stopping.set()
            self.learning_worker.wake.set()
        while True:
            try:
                identifier = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                job = self.store.load("jobs", identifier, Job)
                interrupt(self.store, job, "服务关闭，请重新提交")
            except Exception:
                # Leave the durable record for restart recovery, but continue
                # draining other jobs even if one record cannot be saved.
                log.exception("关闭时无法保存中断状态 %s", identifier)
            finally:
                self.queue.task_done()
                self.slots.release()
        if self.thread.is_alive():
            self.thread.join(timeout=3)

    def execute(self, identifier: str) -> Job:
        return JobExecution(
            self.store,
            identifier,
            stopping=self.stopping,
            transport=self.provider.transport if self.provider else None,
            credentials=self.llm_settings.credentials.resolve,
            renderer=self.renderer,
            memory_sink=self.enqueue_memory,
        ).run()

    def enqueue_memory(self, job_id, interpretation, record):
        from manimagent.memory.models import MemoryOptions

        accepted = record.get("memory_acceptance")
        if accepted is None or not accepted["options"]["learn"]:
            record["memory_save"] = {"state": "disabled"}
            return
        request = record["request"]
        source = request.get("source", request)
        try:
            task = self.learning.enqueue(
                job_id,
                list(interpretation.proposals),
                MemoryOptions.model_validate(accepted["options"]),
                accepted["sequence"],
                source["instruction"],
            )
            record["memory_save"] = {"state": task["state"], "job_id": job_id}
            self.learning_worker.wake.set()
        except Exception as exc:
            # The validated interpretation is already durable and remains reviewable.
            # A storage failure in this independent branch must not change answer quality.
            log.exception("已验证的偏好候选未能排入提交队列 %s", job_id)
            record["memory_save"] = {"state": "failed", "code": "memory_commit_unavailable"}
            record["memory_warnings"] = [
                {
                    "code": "memory_commit_unavailable",
                    "message": "偏好未保存，请检查记忆状态后重试",
                    "reason": exc.error.code if isinstance(exc, ContentError) else "storage_error",
                }
            ]

    def unqueued_memory_status(self, job_id, *, job=None):
        """Expose a validated candidate whose independent queue insertion failed."""
        job = job or self.store.load("jobs", job_id, Job)
        record = self.store.load_record(job_id)
        status = record.get("memory_save", {})
        if status.get("state") not in {"failed", "pending"} or "interpretation" not in record:
            raise ContentError("memory_learning_not_found", "没有可恢复的偏好保存任务", 404)
        if record.get("pipeline_version") != "fixed-2" or not record.get(
            "memory_acceptance", {}
        ).get("options", {}).get("learn"):
            raise ContentError("invalid_record", "偏好保存记录没有有效学习授权", 500)
        source = record["request"].get("source", record["request"])
        options = record["memory_acceptance"]["options"]
        if self.memory.was_withdrawn(job_id):
            return {
                "job_id": job_id,
                "state": "cancelled",
                "attempts": 0,
                "max_attempts": 3,
                "entry_ids": [],
                "promoted_ids": [],
                "counts_known": False,
                "can_cancel": False,
                "can_retry": False,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "error": {
                    "code": "memory_learning_cancelled",
                    "message": "此来源已撤回或保存已取消",
                },
                "origin": "acceptance_withdrawal",
            }
        committed = self.memory.replay(
            session_id=options["session_id"],
            source_id=job_id,
            source_text=source["instruction"],
            scope=options["scope"],
        )
        if committed is not None:
            return {
                "job_id": job_id,
                "state": "completed",
                "attempts": 0,
                "max_attempts": 3,
                "entry_ids": [entry.memory_id for entry in committed],
                "promoted_ids": [],
                "counts_known": False,
                "can_cancel": False,
                "can_retry": False,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "error": None,
                "origin": "source_receipt",
            }
        waiting = status["state"] == "pending" and job.status in {"queued", "running"}
        return {
            "job_id": job_id,
            "state": "waiting" if waiting else "failed",
            "attempts": 0,
            "max_attempts": 3,
            "entry_ids": [],
            "promoted_ids": [],
            "counts_known": False,
            "can_cancel": False,
            "can_retry": not waiting,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "error": None
            if waiting
            else {
                "code": "memory_commit_unavailable",
                "message": "偏好候选已验证但未进入保存队列，可以重试保存。",
                "retryable": True,
            },
            "origin": "durable_interpretation",
        }

    def memory_save_status(self, job):
        if job.memory_save is None or job.memory_save.get("state") == "disabled":
            return job.memory_save
        try:
            return self.learning.get(job.job_id)
        except ContentError as exc:
            if exc.error.code != "memory_learning_not_found":
                raise
            try:
                return self.unqueued_memory_status(job.job_id, job=job)
            except ContentError as missing:
                if missing.error.code != "memory_learning_not_found":
                    raise
                return {
                    "job_id": job.job_id,
                    "state": "unknown",
                    "can_retry": False,
                    "error": {
                        "code": "memory_history_cleared",
                        "message": "保存任务历史已清理，当前结果请在记忆列表核对。",
                    },
                }
        except (sqlite3.Error, OSError):
            # The generation remains readable while the independent memory database
            # is unavailable. Do not turn an unknown commit outcome into success.
            return {
                "job_id": job.job_id,
                "state": "unknown",
                "can_retry": False,
                "error": {
                    "code": "memory_status_unavailable",
                    "message": "偏好保存状态暂不可用，请稍后刷新。",
                },
            }

    def memory_learning_status(self, job_id):
        try:
            return self.learning.get(job_id)
        except ContentError as exc:
            if (
                exc.error.code != "memory_learning_not_found"
                or not self.store.path("records", job_id).exists()
            ):
                raise
            return self.unqueued_memory_status(job_id)

    def memory_learning_list(self):
        from manimagent.memory.learning import MAX_HISTORY

        result = self.learning.list()
        known = {task["job_id"] for task in result["tasks"]}
        # Only bounded recent job bodies are read; return the scan boundary so a
        # recent-history view never claims to audit every historical record.
        paths = sorted(
            (self.store.root / "jobs").glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        recovered = []
        for path in paths[:MAX_HISTORY]:
            job = self.store.load("jobs", path.stem, Job)
            if job.job_id in known or (job.memory_save or {}).get("state") not in {
                "failed",
                "pending",
            }:
                continue
            try:
                self.learning.get(job.job_id)
            except ContentError as exc:
                if exc.error.code != "memory_learning_not_found":
                    raise
                recovered.append(self.unqueued_memory_status(job.job_id, job=job))
        for task in recovered:
            result["counts"][task["state"]] = result["counts"].get(task["state"], 0) + 1
        result["tasks"] = sorted(
            [*result["tasks"], *recovered], key=lambda task: task["updated_at"], reverse=True
        )[:20]
        result["unqueued_scan"] = {
            "examined": min(len(paths), MAX_HISTORY),
            "limit": MAX_HISTORY,
            "older_jobs_omitted": len(paths) > MAX_HISTORY,
        }
        return result

    def retry_memory_save(self, job_id):
        from manimagent.personalization import Interpretation

        with self.lock:
            if self.stopping.is_set():
                raise ContentError("interrupted", "服务已停止", 503)
            try:
                self.learning.get(job_id)
            except ContentError as exc:
                if exc.error.code != "memory_learning_not_found":
                    raise
            else:
                task = self.learning.retry(job_id)
                self.learning_worker.wake.set()
                return task
            job = self.store.load("jobs", job_id, Job)
            status = self.unqueued_memory_status(job_id, job=job)
            if not status["can_retry"]:
                raise ContentError(
                    "memory_learning_conflict", "偏好正在提交或已经提交，不能重新排队", 409
                )
            record = self.store.load_record(job_id)
            interpretation = Interpretation.model_validate(record["interpretation"])
            source = record["request"].get("source", record["request"])
            interpretation.validate_source(
                source["instruction"], record["memory_acceptance"]["options"]["scope"]
            )
            self.enqueue_memory(job_id, interpretation, record)
            self.store.save("records", job_id, record)
            job.memory_save = dict(record["memory_save"])
            save_job(self.store, job)
            if job.memory_save["state"] == "failed":
                raise ContentError(
                    "memory_commit_unavailable", "偏好仍未进入保存队列，请检查存储后重试", 503
                )
            return self.learning.get(job_id)

    def configuration(self):
        with self.lock:
            return self.llm_settings.view(self.settings)

    def configure(self, request):
        with self.lock:
            self.settings = self.llm_settings.update(request, self.settings)
            return self.llm_settings.view(self.settings)

    def probe_model(self, request):
        with self.lock:
            snapshot = self.settings.model_copy(deep=True)
        return self.llm_settings.probe(
            request, snapshot, self.provider.transport if self.provider else None
        )

    def speech_configuration(self):
        with self.lock:
            return self.tts_settings.view(self.settings)

    def configure_speech(self, request):
        with self.lock:
            self.settings = self.tts_settings.update(request, self.settings)
            return self.tts_settings.view(self.settings)

    def probe_speech(self, request):
        with self.lock:
            snapshot = self.settings.model_copy(deep=True)
        return self.tts_settings.probe(
            request, snapshot, self.provider.transport if self.provider else None
        )
