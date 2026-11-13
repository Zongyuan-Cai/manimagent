import json
from copy import deepcopy

import pytest

from manimagent.memory.projection import user_context
from manimagent.memory.store import MemoryStore
from manimagent.personalization import RequestIntent, compile_profile
from tests.support.memory import Clock, observe


@pytest.mark.parametrize("key", ["snapshot", "user_memory", "recent_user_memory"])
@pytest.mark.parametrize("location", ["shared", "current"])
def test_raw_memory_cannot_bypass_compiled_profile(key, location):
    raw = {key: {"entries": []}}
    with pytest.raises(ValueError, match="compile an EffectiveProfile"):
        user_context(
            raw if location == "shared" else {},
            role="answer_writer",
            **(raw if location == "current" else {}),
        )


def test_reaffirmation_does_not_change_compiled_stage_payload(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path / "memory.sqlite3", clock=clock)
    first = observe(store, field="explanation.detail", value="detailed", intent="default")
    request = RequestIntent(overrides=(), exclude_memory=(), issues=())
    before = store.snapshot("one")
    original = deepcopy(before)
    profile = compile_profile(request, before).model_dump(mode="json")
    payload = user_context(
        {"materials": []}, role="answer_writer", effective_profile=profile, instruction="解题"
    )
    clock.advance()
    observe(store, source="again", session="two", field="explanation.detail", value="detailed")
    after = store.snapshot("three")
    assert after != before and before == original
    other = compile_profile(request, after).model_dump(mode="json")
    assert (
        user_context(
            {"materials": []}, role="answer_writer", effective_profile=other, instruction="解题"
        )
        == payload
    )
    assert first.memory_id not in json.dumps(payload)
    assert "captured_at" not in json.dumps(payload)


@pytest.mark.parametrize(
    "role", ["material_reader", "request_interpreter", "answer_checker", "plan_checker"]
)
def test_fact_and_input_checks_do_not_receive_personalization(role):
    profile = compile_profile(
        RequestIntent(overrides=(), exclude_memory=(), issues=()), None
    ).model_dump(mode="json")
    assert user_context({"content": "given"}, role=role, effective_profile=profile) == {
        "content": "given"
    }


def test_visual_parameters_only_reach_visual_generation():
    profile = compile_profile(
        RequestIntent(overrides=(), exclude_memory=(), issues=()), None
    ).model_dump(mode="json")
    writer = user_context({}, role="answer_writer", effective_profile=profile)
    scene = user_context({}, role="scene_generator", effective_profile=profile)
    assert set(writer) == {"teaching_spec"}
    assert set(scene) == {"teaching_spec", "visual_spec"}
