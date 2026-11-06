"""Run persisted user-memory learning after publication, outside generation budgets."""

from __future__ import annotations

import logging
import threading
import time

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.memory.models import MemoryOptions
from manimagent.memory.service import extract_and_observe
from manimagent.schemas import Answer, Generation, Job, Usage

log = logging.getLogger(__name__)


class MemoryLearningWorker:
    def __init__(self, manager):
        self.manager = manager
        self.wake = threading.Event()

    def reconcile(self, job_id):
        # Acceptance writes SQLite and JSON under the manager lock. Never mistake
        # a just-enqueued event for an orphan before its parent is published.
        with self.manager.lock:
            self._reconcile(job_id)

    def _reconcile(self, job_id):
        manager = self.manager
        try:
            task = manager.learning.get(job_id)
        except ContentError as exc:
            if exc.http_status == 404:
                return
            raise
        if task["state"] != "waiting":
            return
        try:
            job = manager.store.load("jobs", job_id, Job)
            if job.status in {"queued", "running"}:
                return
            answer_id = None
            if job.result and job.result.type == "answer":
                answer_id = job.result.id
            elif job.resource_ref and job.resource_ref.type == "generation":
                generation = manager.store.load("generations", job.resource_ref.id, Generation)
                answer_id = generation.answer_id
            if answer_id is not None:
                manager.store.load("answers", answer_id, Answer)
                manager.learning.activate(job_id)
                self.wake.set()
                return
        except ContentError as exc:
            if exc.http_status != 404:
                raise
        manager.learning.cancel(job_id, "answer_not_published")

    def run(self):
        manager = self.manager
        maintenance_at = 0.0
        while not manager.stopping.is_set():
            try:
                if time.monotonic() >= maintenance_at:
                    manager.memory.cleanup()
                    manager.learning.cleanup()
                    # Retry interrupted bookkeeping without replaying model requests.
                    for job_id in manager.learning.waiting_ids():
                        self.reconcile(job_id)
                    maintenance_at = time.monotonic() + 60
                # Give queued foreground work priority. Already running learning
                # never owns the foreground worker or its budget.
                if (
                    not manager.queue.unfinished_tasks
                    and manager.learning.has_queued()
                    and manager.memory_slots.acquire(False)
                ):
                    try:
                        task = manager.learning.claim()
                        if task is not None:
                            self.execute(task)
                            continue
                    finally:
                        manager.memory_slots.release()
            except Exception:
                log.exception("后台记忆处理失败，已保留持久化状态")
            self.wake.wait(1)
            self.wake.clear()

    def execute(self, task):
        manager = self.manager
        usage = Usage()
        started = time.monotonic()
        state, error = "completed", None
        try:
            if manager.stopping.is_set():
                raise InterruptedError()
            if not task["text"].strip():
                state = "skipped"
            else:
                record = manager.store.load_record(task["job_id"])
                settings = Settings.model_validate(record["settings"])
                settings.task_timeout = settings.memory_learning_timeout
                settings.model_timeout = min(settings.model_timeout, settings.task_timeout)
                settings.max_model_calls = 1
                settings.max_total_tokens = settings.memory_learning_max_tokens
                budget = Budget(settings, usage, manager.stopping)
                budget.retry_allowance = 0
                provider = ChatProvider(
                    settings,
                    manager.provider.transport if manager.provider else None,
                    credential_resolver=manager.llm_settings.credentials.resolve,
                )
                extract_and_observe(
                    manager.memory,
                    provider,
                    budget,
                    task["text"],
                    MemoryOptions.model_validate(task["options"]),
                    task["job_id"],
                    expected_revision=task["revision"],
                    learning_job_id=task["job_id"],
                )
        except ContentError as exc:
            state = "failed"
            error = {"code": exc.error.code, "message": exc.error.message}
        except ProviderError as exc:
            state = "failed"
            error = {"code": exc.code, "message": "记忆提取服务未返回可用结果，可检查配置后重试"}
        except BudgetExceeded:
            state = "failed"
            error = {"code": "memory_budget_exceeded", "message": "本次沉淀已达到时间或用量上限"}
        except InterruptedError:
            state = "failed"
            error = {"code": "interrupted", "message": "服务关闭中断了沉淀，可手动重试"}
        except Exception:
            log.exception("记忆沉淀失败 %s", task["job_id"])
            state = "failed"
            error = {"code": "memory_internal_error", "message": "沉淀未完成，请检查服务日志后重试"}
        finally:
            usage.elapsed_seconds = round(time.monotonic() - started, 3)
            manager.learning.finish(task["job_id"], state, usage=usage, error=error)
