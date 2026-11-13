import copy

import pytest
from pydantic import ValidationError

from manimagent.personalization import (
    CurrentPreference,
    Interpretation,
    MemoryExclusion,
    MemoryProposal,
    PreferenceValue,
    RequestIntent,
    compile_profile,
)


def intent(**values):
    return RequestIntent(overrides=(), exclude_memory=(), issues=(), **values)


def entry(value, *, scope="global", layer="deep", sequence=1):
    return {
        "field": "visual.palette",
        "value": value,
        "scope": scope,
        "layer": layer,
        "memory_id": f"m_{scope}_{value}",
        "state": "active",
        "accepted_sequence": sequence,
        "updated_at": "2026-11-13T00:00:00+00:00",
    }


def test_priority_is_current_then_scope_then_stability_without_mutating_snapshot():
    snapshot = {
        "entries": [
            entry("blue_gold"),
            entry("standard", scope="course.math"),
            entry("high_contrast", scope="course.math", layer="short_term", sequence=100),
        ]
    }
    original = copy.deepcopy(snapshot)
    profile = compile_profile(intent(), snapshot, "topic.math.limits")
    assert profile.visual.theme == "standard"
    assert (
        next(a for a in profile.applications if a.field == "visual.palette").scope == "course.math"
    )
    current = RequestIntent(
        overrides=(
            CurrentPreference(field="visual.palette", value="high_contrast", quote="这次高对比"),
        ),
        exclude_memory=(),
        issues=(),
    )
    assert compile_profile(current, snapshot, "topic.math.limits").visual.theme == "high_contrast"
    assert snapshot == original


def test_forgetting_or_ignoring_old_value_changes_current_projection_only():
    snapshot = {"entries": [entry("blue_gold")]}
    request = RequestIntent(
        overrides=(),
        exclude_memory=(
            MemoryExclusion(field="visual.palette", value="blue_gold", quote="忘记蓝金"),
        ),
        issues=(),
    )
    assert compile_profile(request, snapshot).visual.theme == "standard"
    assert snapshot["entries"][0]["value"] == "blue_gold"


def test_field_values_and_scopes_are_closed_and_evidence_is_exact():
    with pytest.raises(ValidationError):
        PreferenceValue(field="visual.palette", value="detailed")
    with pytest.raises(ValidationError):
        PreferenceValue(field="mastery.limit", value="mastered")
    result = Interpretation(
        intent=intent(),
        proposals=(
            MemoryProposal(
                field="visual.palette", value="blue_gold", quote="我偏好蓝金", scope="course.math"
            ),
        ),
    )
    with pytest.raises(ValueError, match="范围"):
        result.validate_source("我偏好蓝金", "global")
    with pytest.raises(ValueError, match="原文"):
        result.validate_source("教材说某人偏好蓝金", "course.math")
    assert result.validate_source("我偏好蓝金", "course.math") is result


def test_disabled_memory_uses_current_requirements_and_product_defaults():
    request = RequestIntent(
        overrides=(
            CurrentPreference(field="explanation.detail", value="concise", quote="这次简洁"),
        ),
        exclude_memory=(),
        issues=(),
    )
    profile = compile_profile(request, None)
    assert profile.teaching.detail == "concise"
    assert profile.visual.theme == "standard"
    assert all(a.origin != "memory" for a in profile.applications)


def test_unknown_frozen_field_is_an_error_not_silent_personalization():
    snapshot = {"entries": [{**entry("blue_gold"), "field": "custom.style"}]}
    with pytest.raises(ValidationError):
        compile_profile(intent(), snapshot)


def test_derived_forget_exclusions_remain_valid_for_restart(monkeypatch):
    from manimagent.budget import Budget
    from manimagent.config import Settings
    from manimagent.llm.client import ChatProvider
    from manimagent.personalization import interpret_request
    from manimagent.schemas import Usage

    quote = "忘记蓝金"
    candidate = Interpretation(
        intent=RequestIntent(
            overrides=(),
            issues=(),
            exclude_memory=tuple(
                MemoryExclusion(
                    field="visual.palette", value="blue_gold", scope="global", quote=quote
                )
                for _ in range(20)
            ),
        ),
        proposals=(
            MemoryProposal(
                field="visual.palette",
                value="blue_gold",
                scope="global",
                quote=quote,
                intent="forget",
            ),
        ),
    )
    monkeypatch.setattr(ChatProvider, "complete", lambda *args, **kwargs: candidate)
    settings = Settings()
    result = interpret_request(ChatProvider(settings), Budget(settings, Usage()), quote)
    assert len(result.intent.exclude_memory) == 1
    assert Interpretation.model_validate_json(result.model_dump_json()) == result


def test_deriving_exclusions_cannot_commit_an_oversized_interpretation(monkeypatch):
    from manimagent.budget import Budget
    from manimagent.config import Settings
    from manimagent.errors import ContentError
    from manimagent.llm.client import ChatProvider
    from manimagent.personalization import FIELD_VALUES, interpret_request
    from manimagent.schemas import Usage

    scopes = ("global", "course.math", "topic.math.limits")
    excludes = [
        MemoryExclusion(field=f, value=v, scope="global", quote="原句")
        for f, values in FIELD_VALUES.items()
        for v in values
    ]
    proposals = [
        MemoryProposal(field=f, value=values[0], scope=scope, intent="forget", quote="原句")
        for scope in scopes
        for f, values in FIELD_VALUES.items()
    ][:20]
    candidate = Interpretation(
        intent=RequestIntent(overrides=(), exclude_memory=tuple(excludes), issues=()),
        proposals=tuple(proposals),
    )
    monkeypatch.setattr(ChatProvider, "complete", lambda *args, **kwargs: candidate)
    settings = Settings()
    with pytest.raises(ContentError, match="exclude_memory"):
        interpret_request(ChatProvider(settings), Budget(settings, Usage()), "原句", scopes[-1])
