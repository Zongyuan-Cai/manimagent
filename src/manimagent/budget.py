from __future__ import annotations

import time
from threading import Event

from manimagent.config import Settings
from manimagent.schemas import Usage


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, settings: Settings, usage: Usage, stop: Event | None = None):
        self.settings = settings
        self.usage = usage
        self.started = time.monotonic()
        self.deadline = self.started + settings.task_timeout
        self.stop = stop
        self.retry_allowance: int | None = None

    def allow_retry(self) -> bool:
        if self.retry_allowance is None:
            return True
        if self.retry_allowance <= 0:
            return False
        self.retry_allowance -= 1
        return True

    def remaining(self, deadline: float | None = None) -> float:
        if self.stop is not None and self.stop.is_set():
            raise InterruptedError("服务已停止，任务中断")
        remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
        if remaining <= 0:
            raise BudgetExceeded("已达到任务或场景时间上限")
        self.usage.elapsed_seconds = round(time.monotonic() - self.started, 3)
        return remaining

    def before_call(self, deadline: float | None = None) -> float:
        remaining = self.remaining(deadline)
        if self.usage.model_calls >= self.settings.max_model_calls:
            raise BudgetExceeded("已达到模型调用次数上限")
        if self.usage.input_tokens + self.usage.output_tokens >= self.settings.max_total_tokens:
            raise BudgetExceeded("已达到模型使用量上限")
        self.usage.model_calls += 1
        return min(remaining, self.settings.model_timeout)
