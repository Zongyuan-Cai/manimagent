from __future__ import annotations

import logging
import queue
import threading

from manimagent.schemas import GenerationRequest, RunManifest
from manimagent.workflow import GenerationEngine

log = logging.getLogger(__name__)


class QueueFull(RuntimeError):
    pass


class JobManager:
    def __init__(self, engine: GenerationEngine):
        self.engine = engine
        self.store = engine.store
        capacity = engine.settings.max_waiting + 1
        self.queue: queue.Queue[str] = queue.Queue(maxsize=capacity)
        self.slots = threading.BoundedSemaphore(capacity)
        self.lock = threading.Lock()
        self.closed = False
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._work, daemon=True, name="manimagent-worker")

    def start(self) -> None:
        self.store.recover()
        self.thread.start()

    def submit(self, request: GenerationRequest) -> RunManifest:
        with self.lock:
            if self.closed or not self.slots.acquire(blocking=False):
                raise QueueFull("任务队列已满，请稍后重试")
            try:
                manifest = self.store.create(request)
                self.queue.put_nowait(manifest.job_id)
                return manifest
            except Exception:
                self.slots.release()
                raise

    def _work(self) -> None:
        while not self.stopping.is_set():
            try:
                job_id = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.engine.execute(job_id)
            except Exception:
                log.exception("Worker failed on job %s", job_id)
                manifest = self.store.load(job_id)
                manifest.status, manifest.message = "failed", "后台任务执行失败"
                self.store.save(manifest)
            finally:
                self.queue.task_done()
                self.slots.release()

    def stop(self) -> None:
        with self.lock:
            self.closed = True
            self.stopping.set()
        self.engine.stop()
        while True:
            try:
                job_id = self.queue.get_nowait()
            except queue.Empty:
                break
            manifest = self.store.load(job_id)
            manifest.status, manifest.message = "interrupted", "服务关闭，请重新提交"
            self.store.save(manifest)
            self.queue.task_done()
            self.slots.release()
        self.thread.join(timeout=3)
