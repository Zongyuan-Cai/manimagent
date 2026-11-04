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

from .execution import JobExecution
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
        self.settings = self.llm_settings.effective(settings)
        self.provider = provider
        self.renderer = renderer or NativeRenderer()
        self.queue: queue.Queue[str] = queue.Queue()
        self.slots = threading.BoundedSemaphore(settings.max_waiting + 1)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
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
            self.thread.start()
        except BaseException:
            self.instance.release()
            raise

    def readiness(self, settings: Settings | None = None) -> list[str]:
        cfg = settings or self.settings
        roles = ("answer_writer", "answer_checker") if cfg.answer_check else ("answer_writer",)
        return cfg.configuration_errors(roles, self.llm_settings.credentials.resolve)

    def validate(self, kind: str, request, settings: Settings) -> None:
        service = MaterialService(self.store, settings)
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
                raise ContentError("queue_full", "文字任务队列已满，请稍后重试", 429)
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
                save_job(self.store, job)
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
        try:
            self._work_until_stopped()
        finally:
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
                log.exception("文字 Worker 无法保存任务 %s", identifier)
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

    def stop(self) -> None:
        with self.lock:
            self.stopping.set()
        while True:
            try:
                identifier = self.queue.get_nowait()
            except queue.Empty:
                break
            job = self.store.load("jobs", identifier, Job)
            interrupt(self.store, job, "服务关闭，请重新提交")
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
