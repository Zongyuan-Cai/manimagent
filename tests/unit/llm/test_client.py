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


def test_corrupt_compressed_response_is_not_reported_as_a_network_timeout():
    client, budget = provider(
        lambda _: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, content=b"private upstream details"
        )
    )
    trace = []
    client.trace = trace.append
    with pytest.raises(ProviderError) as exc:
        client.complete_text("answer_writer", "answer", {}, budget)
    assert exc.value.code == "invalid_provider_response"
    assert "private upstream" not in str(exc.value)
    assert budget.usage.model_calls == 2
    assert len([event for event in trace if event.get("event") == "call_summary"]) == 2


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


def test_oversized_repair_fails_without_dropping_the_candidate():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        content = json.dumps({"ok": "x" * 5000})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    client, budget = provider(handler)
    client.settings.models["text"].max_output_tokens = 256
    client.settings.models["text"].context_window = 4096
    with pytest.raises(ProviderError) as exc:
        client.complete("answer_checker", "check", {}, Check, budget)
    assert exc.value.code == "context_budget_exceeded"
    assert len(requests) == budget.usage.model_calls == 1


@pytest.mark.parametrize(
    "usage",
    [[], "", False, 0, {"prompt_tokens": "10"}, {"completion_tokens": -1}, {"prompt_tokens": True}],
)
def test_malformed_usage_cannot_succeed_with_unaccounted_tokens(usage):
    client, budget = provider(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": usage,
            },
        )
    )
    with pytest.raises(ProviderError) as exc:
        client.complete_text("answer_writer", "answer", {}, budget)
    assert exc.value.code == "invalid_provider_response"


def test_no_auth_request_does_not_read_a_stale_credential():
    client, budget = provider(
        lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}
        )
    )

    def unavailable(_reference):
        raise AssertionError("No-auth requests must not resolve a credential")

    client.credential_resolver = unavailable
    assert client.complete_text("answer_writer", "answer", {}, budget) == "answer"


def test_disabled_retry_reports_the_schema_error_without_building_repair_context():
    client, budget = provider(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"ok": "x" * 5000})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )
    )
    client.settings.models["text"].context_window = 1024
    budget.retry_allowance = 0
    with pytest.raises(ProviderError) as exc:
        client.complete("answer_checker", "check", {}, Check, budget)
    assert exc.value.code == "invalid_provider_response"
    assert budget.usage.model_calls == 1
