from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image

from manimagent.budget import Budget
from manimagent.config import ModelConfig, Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from manimagent.materials.service import MaterialService
from manimagent.materials.vision import read_visual
from manimagent.schemas import ParseRequest, Usage

VISUAL = {"label": "第一页", "locator": {"page": 1}, "media_type": "image/jpeg", "data": "eA=="}


def settings(**kwargs):
    return Settings(
        models={
            "text": ModelConfig(
                base_url="https://vision.invalid/v1",
                model="vision",
                auth_mode="none",
                supports_vision=True,
                max_output_tokens=2048,
            )
        },
        **kwargs,
    )


@pytest.mark.parametrize(
    "limits", [{"max_model_calls": 2}, {"max_total_tokens": 3000}, {"task_timeout": 30}]
)
def test_visual_reading_reserves_answer_budget_before_paid_call(limits):
    cfg = settings(**limits)
    calls = []
    provider = ChatProvider(cfg, httpx.MockTransport(lambda request: calls.append(request)))
    budget = Budget(cfg, Usage())
    with pytest.raises(ContentError) as exc:
        read_visual(VISUAL, provider=provider, budget=budget, index=0, file_id="file_" + "a" * 32)
    assert exc.value.error.code == "budget_exceeded"
    assert not calls and budget.usage.model_calls == 0


def test_visual_format_failure_does_not_retry_or_change_parent_budget():
    cfg = settings()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "bad json"}, "finish_reason": "stop"}]}
        )

    provider = ChatProvider(cfg, httpx.MockTransport(handler))
    budget = Budget(cfg, Usage())
    budget.retry_allowance = 2
    deadline = budget.deadline
    with pytest.raises(ContentError):
        read_visual(VISUAL, provider=provider, budget=budget, index=0, file_id="file_" + "a" * 32)
    assert len(calls) == 1 and budget.usage.model_calls == 1
    assert budget.retry_allowance == 2 and budget.deadline == deadline


@pytest.mark.parametrize(
    "limits,visual_count,expected_code,expected_calls",
    [({}, 1, "provider_rate_limited", 1), ({"max_model_calls": 3}, 2, "budget_exceeded", 0)],
)
def test_visual_failure_identifies_file_and_preserves_budget(
    tmp_path, monkeypatch, limits, visual_count, expected_code, expected_calls
):
    cfg = settings(**limits)
    store = ContentStore(tmp_path, cfg.max_upload_bytes)
    raw = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(raw, format="PNG")
    raw.seek(0)
    asset = store.put_file(raw, "截图.png")
    monkeypatch.setattr(
        "manimagent.materials.service.parse_isolated",
        lambda *args: {
            "visuals": [VISUAL] * visual_count,
            "blocks": [],
            "issues": [],
            "coverage": {"state": "complete"},
        },
    )
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, text="remote-secret")

    provider = ChatProvider(cfg, httpx.MockTransport(respond))
    request = ParseRequest(inputs=[{"type": "file", "file_id": asset.file_id}])
    with pytest.raises(ContentError) as exc:
        MaterialService(store, cfg, provider).parse(request, Budget(cfg, Usage()), {})
    assert exc.value.error.details["filename"] == "截图.png"
    assert exc.value.error.code == expected_code
    assert exc.value.error.retryable == (expected_code == "provider_rate_limited")
    assert len(calls) == expected_calls
    assert "remote-secret" not in json.dumps(exc.value.error.model_dump())
