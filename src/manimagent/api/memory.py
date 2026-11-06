"""Local single-user memory management; memory is never inferred from uploaded files."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from manimagent.budget import Budget, BudgetExceeded
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.memory.models import MemoryCandidate, MemoryOptions
from manimagent.memory.service import extract_and_observe
from manimagent.schemas import Usage


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=12000)
    session_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=100)
    scope: str = Field(default="global", min_length=1, max_length=80)

    @field_validator("session_id", "scope")
    @classmethod
    def valid_options(cls, value, info):
        MemoryOptions(
            **{
                info.field_name: value,
                **({"session_id": "validation"} if info.field_name == "scope" else {}),
            }
        )
        return value


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    value: str = Field(min_length=1, max_length=300)

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        return MemoryCandidate(field="validation", value=value, evidence=value).value


def router(manager):
    routes = APIRouter(prefix="/api/v2/memory")

    @routes.get("")
    def list_memory():
        return {
            "entries": manager.memory.list_entries(),
            "policy": manager.memory.policy.model_dump(),
        }

    @routes.post("/observations")
    def observe(request: ObservationRequest):
        if manager.stopping.is_set() or not manager.memory_slots.acquire(blocking=False):
            raise ContentError("memory_busy", "记忆提取正在进行，请稍后重试", 429)
        try:
            with manager.lock:
                settings = manager.settings.model_copy(deep=True)
            errors = settings.configuration_errors(
                ("answer_writer",), manager.llm_settings.credentials.resolve
            )
            if errors:
                raise ContentError("model_unavailable", "；".join(errors), 503)
            settings.task_timeout = min(25, settings.task_timeout)
            settings.max_model_calls = 1
            usage = Usage()
            budget = Budget(settings, usage, manager.stopping)
            provider = ChatProvider(
                settings,
                manager.provider.transport if manager.provider else None,
                credential_resolver=manager.llm_settings.credentials.resolve,
            )
            entries = extract_and_observe(
                manager.memory,
                provider,
                budget,
                request.text,
                MemoryOptions(session_id=request.session_id, scope=request.scope),
                request.source_id,
            )
            return {"entries": entries, "warnings": [], "usage": usage.model_dump()}
        except ProviderError as exc:
            raise ContentError(exc.code, "记忆提取服务未返回可用结果，请稍后重试", 502) from exc
        except BudgetExceeded as exc:
            raise ContentError("memory_budget_exceeded", "记忆提取达到时间或调用上限", 504) from exc
        except InterruptedError as exc:
            raise ContentError("interrupted", "服务已停止", 503) from exc
        finally:
            manager.memory_slots.release()

    @routes.post("/cleanup")
    def cleanup():
        return manager.memory.cleanup()

    @routes.post("/{memory_id}/confirm")
    def confirm(memory_id: str):
        return manager.memory.confirm(memory_id)

    @routes.patch("/{memory_id}")
    def correct(memory_id: str, request: CorrectionRequest):
        return manager.memory.correct(memory_id, request.value)

    @routes.delete("/{memory_id}")
    def delete(memory_id: str):
        manager.memory.delete(memory_id)
        return {"deleted": True}

    return routes
