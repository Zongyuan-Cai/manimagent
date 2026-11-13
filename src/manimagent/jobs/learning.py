"""Commit validated preference proposals independently of teaching publication."""

from __future__ import annotations

import logging
import threading
import time

from manimagent.errors import ContentError
from manimagent.personalization import MemoryProposal
from manimagent.schemas import Usage

log = logging.getLogger(__name__)


class MemoryLearningWorker:
    def __init__(self, manager):
        self.manager = manager
        self.wake = threading.Event()

    def run(self):
        manager = self.manager
        maintenance_at = 0.0
        while not manager.stopping.is_set():
            try:
                if time.monotonic() >= maintenance_at:
                    manager.memory.cleanup()
                    manager.learning.cleanup()
                    maintenance_at = time.monotonic() + 60
                task = manager.learning.claim()
                if task is not None:
                    self.execute(task)
                    continue
            except Exception:
                log.exception("后台偏好提交失败，已保留持久化状态")
            self.wake.wait(1)
            self.wake.clear()

    def execute(self, task):
        manager = self.manager
        started = time.monotonic()
        state, error = "completed", None
        try:
            if manager.stopping.is_set():
                raise InterruptedError()
            manager.memory.observe_proposals(
                [MemoryProposal.model_validate(item) for item in task["proposals"]],
                session_id=task["options"]["session_id"],
                source_id=task["job_id"],
                source_text=task["source_text"],
                scope=task["options"]["scope"],
                sequence=task["sequence"],
                learning_job_id=task["job_id"],
            )
        except ContentError as exc:
            state, error = "failed", {"code": exc.error.code, "message": exc.error.message}
        except InterruptedError:
            state, error = (
                "failed",
                {"code": "interrupted", "message": "提交被服务停止中断，可重试"},
            )
        except Exception:
            log.exception("记忆事务未完成 %s", task["job_id"])
            state, error = (
                "failed",
                {"code": "memory_internal_error", "message": "偏好未保存，请检查服务日志"},
            )
        finally:
            manager.learning.finish(
                task["job_id"],
                state,
                usage=Usage(elapsed_seconds=round(time.monotonic() - started, 3)),
                error=error,
            )
