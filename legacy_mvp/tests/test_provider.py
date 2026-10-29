from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

import manimagent.budget as budget_module
from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import ModelConfig, Settings
from manimagent.providers import ChatProvider, InvalidResponse, ProviderError
from manimagent.schemas import CodeResponse, Usage
from tests.helpers import PNG


def setup_provider(monkeypatch, handler, **kwargs):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-key")
    cfg = ModelConfig(
        base_url="https://model.example/v1", model="test-model", api_key_env="TEST_MODEL_KEY"
    )
    settings = Settings(models={"text": cfg, "vision": cfg}, **kwargs)
    return ChatProvider(settings, httpx.MockTransport(handler)), Budget(settings, Usage())


def response(content, **kwargs):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], **kwargs})


def test_request_images_schema_and_usage(monkeypatch, tmp_path):
    received = []

    def handler(request):
        received.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only-key"
        return response('{"code":"hello"}', usage={"prompt_tokens": 20, "completion_tokens": 5})

    provider, budget = setup_provider(monkeypatch, handler)
    image = tmp_path / "frame.png"
    image.write_bytes(PNG)
    result = provider.complete(
        "vision_checker", "return JSON", {"text": "向量"}, CodeResponse, budget, images=[image]
    )
    assert result.code == "hello"
    assert received[0]["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert received[0]["response_format"] == {"type": "json_object"}
    assert budget.usage.model_calls == 1
    assert budget.usage.input_tokens == 20 and budget.usage.output_tokens == 5


@pytest.mark.parametrize("first", ["invalid", "missing", "transient"])
def test_single_retry_then_valid(monkeypatch, first):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            if first == "transient":
                return httpx.Response(503)
            return response("no JSON" if first == "invalid" else "{}")
        return response('{"code":"fixed"}')

    provider, budget = setup_provider(monkeypatch, handler)
    assert provider.complete("coder", "JSON", {}, CodeResponse, budget).code == "fixed"
    assert len(attempts) == 2 and budget.usage.model_calls == 2


def test_auth_failure_is_not_retried_or_leaked(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(401, text="test-only-key should not leak")

    provider, budget = setup_provider(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        provider.complete("coder", "JSON", {}, CodeResponse, budget)
    assert len(attempts) == 1
    assert "test-only-key" not in str(exc.value)


def test_invalid_response_and_call_budget(monkeypatch):
    provider, budget = setup_provider(monkeypatch, lambda _: response("[]"))
    with pytest.raises(InvalidResponse):
        provider.complete("coder", "JSON", {}, CodeResponse, budget)
    assert budget.usage.model_calls == 2
    provider, budget = setup_provider(monkeypatch, lambda _: response("invalid"), max_model_calls=1)
    with pytest.raises(BudgetExceeded):
        provider.complete("coder", "JSON", {}, CodeResponse, budget)
    assert budget.usage.model_calls == 1


def test_deadline_and_token_budget(monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(budget_module, "time", SimpleNamespace(monotonic=lambda: clock.value))
    budget = Budget(Settings(task_timeout=1), Usage())
    clock.value = 102
    with pytest.raises(BudgetExceeded):
        budget.before_call()
    budget = Budget(Settings(max_total_tokens=1000), Usage(input_tokens=1000))
    with pytest.raises(BudgetExceeded):
        budget.before_call()


def test_http_call_has_a_total_wall_time_limit(monkeypatch):
    async def handler(request):
        await asyncio.sleep(1)
        return response('{"code":"too late"}')

    provider, budget = setup_provider(monkeypatch, handler, model_timeout=0.02)
    started = time.monotonic()
    with pytest.raises(ProviderError):
        provider.complete("coder", "JSON", {}, CodeResponse, budget)
    assert time.monotonic() - started < 0.5
    assert budget.usage.model_calls == 2


def test_reported_token_limit_stops_current_result(monkeypatch):
    provider, budget = setup_provider(
        monkeypatch,
        lambda _: response(
            '{"code":"valid"}', usage={"prompt_tokens": 1500, "completion_tokens": 10}
        ),
        max_total_tokens=1000,
    )
    with pytest.raises(BudgetExceeded):
        provider.complete("coder", "JSON", {}, CodeResponse, budget)
    assert budget.usage.model_calls == 1
