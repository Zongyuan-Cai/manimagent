from __future__ import annotations

import httpx
import pytest

from manimagent.budget import Budget
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.schemas import Usage
from tests.support.pipeline import settings_for


def test_custom_auth_full_url_and_text_finish_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXT_TEST_KEY", "private-test-key")
    settings = settings_for(tmp_path)
    cfg = settings.models["text"]
    cfg.auth_mode, cfg.api_key_env, cfg.api_key_header = (
        "api_key_header",
        "TEXT_TEST_KEY",
        "Gateway-Key",
    )
    cfg.generate_url = "https://model.example/custom/invoke"

    def handler(request):
        assert (
            str(request.url) == cfg.generate_url
            and request.headers["Gateway-Key"] == "private-test-key"
        )
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
        )

    provider = ChatProvider(settings, httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as exc:
        provider.complete_text("answer_writer", "answer", {}, Budget(settings, Usage()))
    assert exc.value.code == "output_truncated"


def test_provider_unknown_usage_and_malformed_messages(tmp_path):
    settings = settings_for(tmp_path)

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "complete"}, "finish_reason": "stop"}]}
        )

    budget = Budget(settings, Usage())
    assert (
        ChatProvider(settings, httpx.MockTransport(handler)).complete_text(
            "answer_writer", "answer", {}, budget
        )
        == "complete"
    )
    assert budget.usage.model_calls == 1 and not budget.usage.tokens_reported

    def bad_handler(request):
        return httpx.Response(200, json={"choices": [{"message": []}]})

    with pytest.raises(ProviderError):
        ChatProvider(settings, httpx.MockTransport(bad_handler)).complete_text(
            "answer_writer", "answer", {}, Budget(settings, Usage())
        )
