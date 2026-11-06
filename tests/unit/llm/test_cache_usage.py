import httpx
import pytest
from pydantic import BaseModel

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import ModelConfig, Settings
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.llm.usage import normalize_usage
from manimagent.schemas import Usage


def configured(handler, **options):
    settings = Settings(
        models={
            "text": ModelConfig(base_url="https://model.example/v1", model="text", auth_mode="none")
        },
        **options,
    )
    events = []
    client = ChatProvider(settings, httpx.MockTransport(handler), trace=events.append)
    return client, Budget(settings, Usage()), events


def result(usage=None, content="complete", **overrides):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage,
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "cache",
    [
        {"prompt_tokens_details": {"cached_tokens": 70}},
        {"prompt_cache_hit_tokens": 70, "prompt_cache_miss_tokens": 30},
        {
            "prompt_tokens_details": {"cached_tokens": 70},
            "prompt_cache_hit_tokens": 70,
            "prompt_cache_miss_tokens": 30,
        },
    ],
)
def test_documented_cache_fields_report_a_subset_of_full_input(cache):
    client, budget, events = configured(
        lambda _: result({"prompt_tokens": 100, "completion_tokens": 10, **cache})
    )
    assert client.complete_text("answer_writer", "instruction", {}, budget) == "complete"
    assert budget.usage.input_tokens == 100 and budget.usage.output_tokens == 10
    assert budget.usage.cached_input_tokens == 70
    assert (
        budget.usage.cache_accounted_input_tokens == 100 and budget.usage.cache_reported_calls == 1
    )
    summary = next(event for event in events if event.get("event") == "call_summary")
    assert summary["role"] == "answer_writer" and summary["attempt"] == 1
    assert summary["http_status"] == 200 and summary["elapsed_seconds"] >= 0
    assert summary["normalized_usage"]["cached_input_tokens"] == 70


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 100},
        {"prompt_tokens": True, "prompt_tokens_details": {"cached_tokens": 0}},
        {"prompt_tokens": "100", "prompt_tokens_details": {"cached_tokens": 50}},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": True}},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": -1}},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 1.5}},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": "50"}},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 101}},
        {"prompt_tokens": 100, "prompt_tokens_details": None},
        {"prompt_tokens": 100, "prompt_tokens_details": []},
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 50},
        {"prompt_tokens": 100, "prompt_cache_miss_tokens": 50},
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 50, "prompt_cache_miss_tokens": 49},
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": 99},
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 101, "prompt_cache_miss_tokens": -1},
        {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 70},
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
        },
        {"prompt_tokens": 100, "cached_tokens": 50},
    ],
)
def test_missing_invalid_or_conflicting_cache_fields_remain_unknown(usage):
    normalized = normalize_usage(usage)
    assert normalized["cached_input_tokens"] is None
    assert normalized["cache_accounted_input_tokens"] is None
    assert normalized["cache_reported"] is False


@pytest.mark.parametrize("prompt", [0, 100])
def test_explicit_zero_hits_are_distinct_from_unknown(prompt):
    client, budget, _ = configured(
        lambda _: result({"prompt_tokens": prompt, "prompt_tokens_details": {"cached_tokens": 0}})
    )
    client.complete_text("answer_writer", "instruction", {}, budget)
    assert budget.usage.cached_input_tokens == 0
    assert budget.usage.cache_reported_calls == 1
    assert budget.usage.cache_accounted_input_tokens == prompt


def test_partial_reporting_keeps_a_separate_known_input_denominator():
    replies = iter(
        [
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 70},
            },
            {"prompt_tokens": 200, "completion_tokens": 20},
            {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 50,
            },
        ]
    )
    client, budget, events = configured(lambda _: result(next(replies)))
    for role in ("answer_writer", "answer_checker", "memory_extractor"):
        client.complete_text(role, "instruction", {}, budget)
    assert budget.usage.model_calls == 3 and budget.usage.input_tokens == 350
    assert budget.usage.cached_input_tokens == 70
    assert (
        budget.usage.cache_reported_calls == 2 and budget.usage.cache_accounted_input_tokens == 150
    )
    summaries = [event for event in events if event.get("event") == "call_summary"]
    assert [event["normalized_usage"]["cached_input_tokens"] for event in summaries] == [
        70,
        None,
        0,
    ]
    assert [event["role"] for event in summaries] == [
        "answer_writer",
        "answer_checker",
        "memory_extractor",
    ]


def test_cache_does_not_reduce_the_token_budget_and_over_budget_response_is_traced():
    client, budget, events = configured(
        lambda _: result(
            {
                "prompt_tokens": 900,
                "completion_tokens": 101,
                "prompt_tokens_details": {"cached_tokens": 900},
            }
        ),
        max_total_tokens=1000,
    )
    with pytest.raises(BudgetExceeded):
        client.complete_text("answer_writer", "instruction", {}, budget)
    assert budget.usage.input_tokens + budget.usage.output_tokens == 1001
    summaries = [event for event in events if event.get("event") == "call_summary"]
    assert len(summaries) == 1 and summaries[0]["normalized_usage"]["cached_input_tokens"] == 900


@pytest.mark.parametrize("failure", ["http", "transport"])
def test_failed_attempts_have_latency_and_unknown_cache_without_invented_misses(failure):
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            if failure == "transport":
                raise httpx.ConnectError("private upstream connection details", request=request)
            return httpx.Response(503, text="private upstream failure body")
        return result({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 60}})

    client, budget, events = configured(respond)
    assert client.complete_text("answer_writer", "instruction", {}, budget) == "complete"
    summaries = [event for event in events if event.get("event") == "call_summary"]
    assert len(summaries) == budget.usage.model_calls == 2
    assert (
        budget.usage.cache_reported_calls == 1 and budget.usage.cache_accounted_input_tokens == 100
    )
    assert summaries[0]["normalized_usage"]["cached_input_tokens"] is None
    assert all(event["elapsed_seconds"] >= 0 for event in summaries)
    assert "private upstream" not in str(events)


def test_authentication_failure_still_emits_one_unknown_summary():
    client, budget, events = configured(lambda _: httpx.Response(401))
    with pytest.raises(ProviderError):
        client.complete_text("answer_writer", "instruction", {}, budget)
    assert budget.usage.cached_input_tokens is None and budget.usage.cache_reported_calls == 0
    summaries = [event for event in events if event.get("event") == "call_summary"]
    assert len(summaries) == 1 and summaries[0]["http_status"] == 401


def test_schema_retry_counts_each_reported_attempt_once():
    class Check(BaseModel):
        ok: bool

    contents = iter(['{"ok":"invalid"}', '{"ok":true}'])
    client, budget, events = configured(
        lambda _: result(
            {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 70}}, next(contents)
        )
    )
    assert client.complete("answer_checker", "instruction", {}, Check, budget).ok
    assert budget.usage.cached_input_tokens == 140 and budget.usage.cache_reported_calls == 2
    assert budget.usage.cache_accounted_input_tokens == 200
    assert len([event for event in events if event.get("event") == "call_summary"]) == 2


def test_historical_usage_without_cache_fields_loads_as_unknown():
    usage = Usage.model_validate(
        {
            "model_calls": 3,
            "input_tokens": 100,
            "output_tokens": 20,
            "tokens_reported": True,
            "elapsed_seconds": 1.2,
        }
    )
    assert usage.cached_input_tokens is None
    assert usage.cache_reported_calls == usage.cache_accounted_input_tokens == 0
