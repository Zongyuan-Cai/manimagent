from copy import deepcopy

import pytest

from manimagent.memory.models import MemoryPolicy
from manimagent.memory.projection import model_memory
from manimagent.memory.store import MemoryStore
from tests.support.memory import Clock, observe


def test_projection_is_unchanged_by_reaffirmation_capture_time_and_other_session(tmp_path):
    clock = Clock()
    store = MemoryStore(tmp_path / "memory.sqlite3", clock=clock)
    first = observe(store, field="explanation.detail", value="详细解释")
    store.confirm(first.memory_id)
    second = observe(store, source="other", field="profile.background", value="本科生")
    store.confirm(second.memory_id)
    before = store.snapshot("s1")
    original = deepcopy(before)
    projected = model_memory(before, "answer_writer")
    clock.advance()
    observe(store, source="again", field="explanation.detail", value="详细解释")
    after = store.snapshot("new-session")
    assert before != after
    assert model_memory(after, "answer_writer") == projected
    assert before == original
    assert projected == {
        "deep": [
            {"field": "explanation.detail", "value": "详细解释", "scope": "global"},
            {"field": "profile.background", "value": "本科生", "scope": "global"},
        ],
        "recent": [],
    }


def test_projection_keeps_selected_winner_scope_and_temporary_semantics(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3", clock=Clock())
    original = observe(store, field="explanation.pace", value="慢一点")
    store.confirm(original.memory_id)
    observe(store, source="override", field="explanation.pace", value="这次快一点", temporary=True)
    observe(
        store, source="scoped", field="custom.preference", value="原句条件不改写", scope="geometry"
    )
    projected = model_memory(store.snapshot("s1", "geometry"), "scene_generator")
    assert projected["deep"] == []
    assert projected["recent"] == [
        {
            "field": "custom.preference",
            "value": "原句条件不改写",
            "scope": "geometry",
            "temporary": False,
        },
        {"field": "explanation.pace", "value": "这次快一点", "scope": "global", "temporary": True},
    ]


@pytest.mark.parametrize(
    "role", ["answer_writer", "answer_checker", "scene_generator", "custom_role"]
)
def test_roles_filter_only_known_irrelevant_fields_and_keep_unknowns(role):
    snapshot = {
        "entries": [
            {"field": field, "value": "原样值", "scope": "global", "layer": "deep"}
            for field in ("visual.palette", "profile.background", "视觉偏好", "custom.preference")
        ]
    }
    fields = {item["field"] for item in model_memory(snapshot, role)["deep"]}
    assert {"profile.background", "视觉偏好", "custom.preference"} <= fields
    assert ("visual.palette" in fields) == (role not in {"answer_writer", "answer_checker"})


@pytest.mark.parametrize("role", ["material_reader", "memory_extractor"])
def test_reading_and_extraction_do_not_receive_personalization(role):
    assert model_memory({"entries": [{"field": "anything"}]}, role) == {"deep": [], "recent": []}
    assert model_memory(None, role) == {"deep": [], "recent": []}


@pytest.mark.parametrize("reading_session", ["unseen", "support"])
@pytest.mark.parametrize("evidence_session", ["new-session", "support"])
def test_bounded_deep_selection_does_not_churn_when_same_fact_gains_evidence(
    tmp_path, reading_session, evidence_session
):
    clock = Clock()
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_snapshot_entries=2), clock)
    for field in ("a", "b", "c"):
        entry = observe(store, source=field, field=field, value="不变", session="support")
        store.confirm(entry.memory_id)
        clock.advance()
    before = model_memory(store.snapshot(reading_session), "answer_writer")
    assert [entry["field"] for entry in before["deep"]] == ["a", "b"]
    for field in ("c", "a", "b"):
        observe(
            store, source=f"repeat-{field}", field=field, value="不变", session=evidence_session
        )
        clock.advance()
        assert model_memory(store.snapshot(reading_session), "answer_writer") == before
