"""Accept, queue and own jobs; execution policy lives in execution.py."""

from __future__ import annotations

import logging
import queue
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
            for job_id in self.learning.waiting_ids():
                self.learning_worker.reconcile(job_id)
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
            if not self.tts_settings.status(settings)["ready"]:
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
                    record["memory_snapshot"] = self.memory.snapshot(
                        options.session_id, options.scope
                    )
                    if options.learn:
                        try:
                            self.learning.enqueue(
                                job.job_id,
                                source.instruction,
                                options,
                                record["memory_snapshot"]["manual_revision"],
                            )
                        except ContentError as exc:
                            record["memory_warnings"] = [
                                {
                                    "code": "memory_learning_not_queued",
                                    "message": "本次后台记忆任务未排入，已有记忆仍可用于生成",
                                    "reason": exc.error.code,
                                }
                            ]
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
                    self.learning.cancel(job.job_id, "answer_not_published")
                except Exception:
                    log.warning("未能撤销未发布任务的记忆事件 %s", job.job_id)
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
                try:
                    self.learning_worker.reconcile(identifier)
                except Exception:
                    log.exception("记忆任务状态暂未更新，将在恢复时重试 %s", identifier)
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
                try:
                    self.learning_worker.reconcile(identifier)
                except Exception:
                    log.exception("关闭时无法更新记忆任务，将在恢复时重试 %s", identifier)
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
        ).run()

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
