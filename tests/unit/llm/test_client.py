import json

import httpx
import pytest
from pydantic import BaseModel

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import ModelConfig, Settings
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.schemas import Usage


class Check(BaseModel):
    ok: bool


def provider(handler, **kwargs):
    settings = Settings(
        models={
            "text": ModelConfig(base_url="https://model.example/v1", model="text", auth_mode="none")
        },
        **kwargs,
    )
    return ChatProvider(settings, httpx.MockTransport(handler)), Budget(settings, Usage())


def test_plain_and_structured_calls_keep_their_output_contracts():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true}' if "response_format" in body else "# 完整回答"
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    client, budget = provider(handler)
    assert client.complete_text("answer_writer", "answer", {}, budget) == "# 完整回答"
    assert client.complete("answer_checker", "check", {}, Check, budget).ok
    assert "response_format" not in requests[0] and "response_format" in requests[1]
    assert budget.usage.model_calls == 2 and budget.usage.input_tokens == 20


@pytest.mark.parametrize(
    "status, code, calls",
    [
        (401, "provider_auth_error", 1),
        (402, "provider_quota_exceeded", 1),
        (429, "provider_rate_limited", 2),
        (503, "provider_unavailable", 2),
    ],
)
def test_http_failure_classification_and_bounded_attempts(status, code, calls):
    client, budget = provider(lambda _: httpx.Response(status, text="private upstream details"))
    with pytest.raises(ProviderError) as exc:
        client.complete_text("answer_writer", "answer", {}, budget)
    assert exc.value.code == code and budget.usage.model_calls == calls
    assert "private upstream" not in str(exc.value)


@pytest.mark.parametrize(
    "finish,code",
    [
        ("length", "output_truncated"),
        ("content_filter", "provider_refusal"),
        ("tool_calls", "capability_unsupported"),
    ],
)
def test_incomplete_responses_never_succeed(finish, code):
    client, budget = provider(
        lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": finish}]}
        )
    )
    with pytest.raises(ProviderError) as exc:
        client.complete_text("answer_writer", "answer", {}, budget)
    assert exc.value.code == code


def test_invalid_json_retries_without_exceeding_the_call_budget():
    client, budget = provider(
        lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": "invalid"}, "finish_reason": "stop"}]}
        ),
        max_model_calls=1,
    )
    with pytest.raises(BudgetExceeded):
        client.complete("answer_checker", "check", {}, Check, budget)
    assert budget.usage.model_calls == 1


def test_checker_also_requires_a_confirmed_stop():
    client, budget = provider(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})
    )
    with pytest.raises(ProviderError):
        client.complete("answer_checker", "check", {}, Check, budget)
    assert budget.usage.model_calls == 2


def test_schema_retry_carries_field_feedback_and_stays_within_retry_budget():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 2:
            assert body["messages"][-2] == {
                "role": "assistant",
                "content": '{"ok":"invalid-boolean"}',
            }
            assert '"loc": ["ok"]' in body["messages"][-1]["content"]
            assert "invalid-boolean" in body["messages"][-1]["content"]
        content = '{"ok":"invalid-boolean"}' if len(requests) == 1 else '{"ok":true}'
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    client, budget = provider(handler)
    budget.retry_allowance = 1
    trace = []
    client.trace = trace.append
    assert client.complete("answer_checker", "check", {}, Check, budget).ok
    assert budget.usage.model_calls == 2 and budget.retry_allowance == 0
    assert (
        len(
            next(event["request"]["messages"] for event in trace if event.get("event") == "request")
        )
        == 2
    )


def test_repair_omits_large_candidate_without_exceeding_context():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 2:
            assert all(m["role"] != "assistant" for m in body["messages"])
            assert '"loc": ["ok"]' in body["messages"][-1]["content"]
            assert sum(len(m["content"].encode()) for m in body["messages"]) + 512 + 256 <= 4096
        content = json.dumps({"ok": "x" * 5000}) if len(requests) == 1 else '{"ok":true}'
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    client, budget = provider(handler)
    client.settings.models["text"].max_output_tokens = 256
    client.settings.models["text"].context_window = 4096
    assert client.complete("answer_checker", "check", {}, Check, budget).ok
