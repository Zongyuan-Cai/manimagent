import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from manimagent.errors import ContentError
from manimagent.memory.models import MemoryCandidate, MemoryOptions, MemoryPolicy
from manimagent.memory.store import MemoryStore
from tests.support.memory import Clock, observe


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return MemoryStore(tmp_path / "memory.sqlite3", clock=clock)


def values(snapshot):
    return {e["field"]: e["value"] for e in snapshot["entries"]}


def test_independent_sessions_promote_and_replay_is_idempotent(store):
    first = observe(store)
    replay = observe(store)
    assert first.memory_id == replay.memory_id
    assert len(replay.evidence) == 1
    for i in range(1, 6):
        current = observe(store, source=f"same-session-{i}")
    assert current.layer == "short_term"
    assert current.independent_sessions == 1
    assert values(store.snapshot("other")) == {"视觉偏好": "先画图"}
    second = observe(store, session="s2", source="second")
    assert second.layer == "short_term"
    third = observe(store, session="s3", source="third")
    assert third.layer == "deep"
    assert third.independent_sessions == 3
    assert values(store.snapshot("new-session")) == {"视觉偏好": "先画图"}


def test_evidence_and_source_fingerprint_validation_is_atomic(store):
    with pytest.raises(ContentError, match="逐字"):
        store.observe(
            [MemoryCandidate(field="风格", value="简洁", evidence="喜欢简洁")],
            session_id="s",
            source_id="r",
            source_text="请解题",
        )
    assert store.list_entries() == []
    observe(store)
    with pytest.raises(ContentError, match="同一来源"):
        observe(store, value="先列公式")
    assert len(store.list_entries()) == 1


def test_recent_short_term_crosses_sessions_but_remains_scope_bound(store):
    observe(store, scope="geometry")
    assert not store.snapshot("s1", "algebra")["entries"]
    assert values(store.snapshot("s2", "geometry")) == {"视觉偏好": "先画图"}
    assert values(store.snapshot("s1", "geometry")) == {"视觉偏好": "先画图"}
    observe(store, source="global", field="语气", value="简洁")
    assert values(store.snapshot("s1", "geometry")) == {"视觉偏好": "先画图", "语气": "简洁"}


def test_same_value_seen_in_multiple_sessions_keeps_all_short_contexts(store):
    observe(store)
    observe(store, session="s2", source="second")
    assert store.snapshot("s1")["entries"]
    assert store.snapshot("s2")["entries"]


def test_scope_specific_overrides_global_deep(store):
    global_entry = observe(store)
    store.confirm(global_entry.memory_id)
    scoped = observe(store, source="geometry", value="先公式", scope="geometry")
    store.confirm(scoped.memory_id)
    assert values(store.snapshot("new", "geometry")) == {"视觉偏好": "先公式"}
    assert values(store.snapshot("new", "algebra")) == {"视觉偏好": "先画图"}


def test_new_short_request_overrides_deep_only_in_its_session(store, clock):
    entry = observe(store)
    store.confirm(entry.memory_id)
    clock.advance()
    recent = observe(store, session="s2", source="exception", value="先公式", temporary=True)
    assert values(store.snapshot("s2")) == {"视觉偏好": "先公式"}
    assert values(store.snapshot("new")) == {"视觉偏好": "先画图"}
    with pytest.raises(ContentError, match="临时"):
        store.confirm(recent.memory_id)
    for i in range(3):
        item = observe(store, session=f"temp{i}", source=f"t{i}", value="先公式", temporary=True)
        assert item.layer == "short_term"
    clock.advance(15)
    assert values(store.snapshot("s2")) == {"视觉偏好": "先画图"}


def test_changed_preference_replaces_only_after_independent_support(store, clock):
    entry = observe(store)
    store.confirm(entry.memory_id)
    for i in range(2):
        clock.advance()
        new = observe(store, session=f"new{i}", source=f"new{i}", value="先公式")
        assert new.layer == "short_term"
        assert values(store.snapshot("unseen")) == {"视觉偏好": "先画图"}
    clock.advance()
    new = observe(store, session="new3", source="new3", value="先公式")
    assert new.layer == "deep"
    assert values(store.snapshot("unseen")) == {"视觉偏好": "先公式"}
    assert entry.memory_id not in [e.memory_id for e in store.list_entries()]


def test_reaffirming_old_deep_does_not_erase_change_candidate(store, clock):
    for i in range(3):
        observe(store, session=f"old{i}", source=f"old{i}")
    clock.advance()
    change = observe(store, session="new1", source="new1", value="先公式")
    observe(store, session="old4", source="old4")
    assert change.memory_id in [e.memory_id for e in store.list_entries()]


def test_confirm_and_correct_are_explicit_deep_updates(store):
    entry = observe(store)
    confirmed = store.confirm(entry.memory_id)
    assert confirmed.confirmed_at
    corrected = store.correct(entry.memory_id, "先解释再画图")
    assert corrected.layer == "deep"
    assert corrected.memory_id != entry.memory_id
    assert corrected.evidence[0].kind == "correction"
    assert values(store.snapshot("new")) == {"视觉偏好": "先解释再画图"}


def test_expiry_applies_before_cleanup_and_deep_does_not_expire(store, clock):
    recent = observe(store)
    deep = observe(store, source="deep", field="背景", value="本科生")
    store.confirm(deep.memory_id)
    clock.advance(14)
    assert values(store.snapshot("s1")) == {"背景": "本科生"}
    with pytest.raises(ContentError):
        store.confirm(recent.memory_id)
    assert store.cleanup() == {"expired": 1, "superseded": 0, "evicted": 0, "remaining": 1}
    clock.advance(1000)
    assert values(store.snapshot("s2")) == {"背景": "本科生"}


def test_delete_removes_content_and_sessions_and_replay_cannot_restore(store):
    entry = observe(store)
    store.delete(entry.memory_id)
    assert not store.list_entries()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert (
        store.observe(
            [MemoryCandidate(field="视觉偏好", value="先画图", evidence="我希望先画图。")],
            session_id="s1",
            source_id="r1",
            source_text="我希望先画图。",
        )
        == []
    )
    assert not store.snapshot("s1")["entries"]


def test_entry_capacity_evicts_short_term_but_protects_deep(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_entries=2), clock)
    deep = observe(store)
    store.confirm(deep.memory_id)
    old = observe(store, source="old", field="旧", value="a")
    clock.advance()
    newest = observe(store, source="new", field="新", value="b")
    assert {e.memory_id for e in store.list_entries()} == {deep.memory_id, newest.memory_id}
    assert old.memory_id not in {e.memory_id for e in store.list_entries()}
    store.confirm(newest.memory_id)
    with pytest.raises(ContentError, match="容量"):
        observe(store, source="overflow", field="第三项", value="c")
    assert len(store.list_entries()) == 2


def test_correcting_full_deep_store_can_replace_without_capacity_error(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_entries=1), clock)
    entry = observe(store)
    store.confirm(entry.memory_id)
    updated = store.correct(entry.memory_id, "先公式")
    assert len(store.list_entries()) == 1
    assert updated.layer == "deep"


def test_source_ledger_capacity_preserves_idempotence_and_other_actions(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_sources=1), clock)
    entry = observe(store)
    assert observe(store).memory_id == entry.memory_id
    with pytest.raises(ContentError, match="来源索引"):
        observe(store, source="r2")
    store.confirm(entry.memory_id)
    assert store.snapshot("unseen")["entries"]
    store.delete(entry.memory_id)
    assert not store.list_entries()


def test_evidence_is_bounded_without_inflating_independent_support(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_evidence_per_entry=2), clock)
    for i in range(5):
        entry = observe(store, source=f"r{i}")
    assert len(entry.evidence) == 2
    assert entry.independent_sessions == 1
    observe(store, session="s2", source="s2")
    entry = observe(store, source="s1-again")
    assert entry.independent_sessions == 2
    assert entry.layer == "short_term"


def test_snapshot_is_bounded_deterministic_and_frozen(tmp_path, clock):
    policy = MemoryPolicy(max_snapshot_entries=2, max_snapshot_chars=800)
    store = MemoryStore(tmp_path / "memory.sqlite3", policy, clock)
    for i in range(5):
        observe(store, source=f"r{i}", field=f"field{i}", value="value" * 20)
    snapshot = store.snapshot("s1")
    assert len(snapshot["entries"]) <= 2
    assert len(json.dumps(snapshot, ensure_ascii=False)) <= 800
    assert snapshot == store.snapshot("s1")
    old_snapshot = json.dumps(snapshot)
    store.correct(snapshot["entries"][0]["memory_id"], "new")
    assert json.dumps(snapshot) == old_snapshot


def test_reopen_and_concurrent_instances_keep_all_evidence(tmp_path, clock):
    path = tmp_path / "memory.sqlite3"
    stores = [MemoryStore(path, clock=clock) for _ in range(4)]

    def write(i):
        return observe(stores[i % 4], session=f"s{i}", source=f"r{i}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    entries = MemoryStore(path, clock=clock).list_entries()
    assert len(entries) == 1
    assert entries[0].independent_sessions == 12
    assert len(entries[0].evidence) == 12
    assert entries[0].layer == "deep"


def test_exact_normalization_does_not_semantically_merge(store):
    first = observe(store, value="  先  画图  ")
    second = observe(store, source="second", value="先 画图")
    assert second.memory_id == first.memory_id
    third = observe(store, source="third", value="图像优先")
    assert third.memory_id != first.memory_id


def test_invalid_options_reject_paths_and_blank_content():
    with pytest.raises(ValidationError):
        MemoryOptions(session_id="../other")
    with pytest.raises(ValidationError):
        MemoryCandidate(field=" ", value="x", evidence="x")


def test_replay_skips_extraction_and_checks_input_independently(store):
    args = {"session_id": "s1", "source_id": "r1", "source_text": "我希望先画图。"}
    assert store.replay(**args) is None
    entry = observe(store)
    assert store.replay(**args)[0].memory_id == entry.memory_id
    with pytest.raises(ContentError, match="同一来源"):
        store.replay(**{**args, "source_text": "现在用公式"})
    store.delete(entry.memory_id)
    assert store.replay(**args) == []


def test_replay_of_empty_extraction_is_distinct_from_unseen_source(store):
    args = {"session_id": "s1", "source_id": "r1", "source_text": "计算1+1"}
    store.observe([], **args)
    assert store.replay(**args) == []


def test_current_session_uses_observation_order_when_timestamps_tie(store):
    entry = observe(store)
    store.confirm(entry.memory_id)
    observe(store, source="newer", value="先公式", temporary=True)
    assert values(store.snapshot("s1")) == {"视觉偏好": "先公式"}
    observe(store, source="return-to-original")
    assert values(store.snapshot("s1")) == {"视觉偏好": "先画图"}


def test_old_session_support_does_not_get_renewed_by_another_session(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(promotion_sessions=10), clock)
    stable = observe(store, session="default", source="stable", value="先公式")
    store.confirm(stable.memory_id)
    observe(store)
    clock.advance(10)
    observe(store, session="s2", source="r2")
    clock.advance(5)
    assert values(store.snapshot("s1")) == {"视觉偏好": "先公式"}
    assert values(store.snapshot("s2")) == {"视觉偏好": "先画图"}


def test_temporary_never_leaks_when_recent_non_temporary_crosses_sessions(store):
    observe(store, field="背景", value="本科生")
    observe(store, source="temporary", field="节奏", value="今天快一点", temporary=True)
    assert values(store.snapshot("s2")) == {"背景": "本科生"}
    assert values(store.snapshot("s1")) == {"背景": "本科生", "节奏": "今天快一点"}


def test_current_global_request_overrides_scoped_deep_even_in_same_session(store):
    scoped = observe(store, scope="geometry")
    store.confirm(scoped.memory_id)
    observe(store, source="current", value="这次先公式", temporary=True)
    assert values(store.snapshot("s1", "geometry")) == {"视觉偏好": "这次先公式"}
    assert values(store.snapshot("other", "geometry")) == {"视觉偏好": "先画图"}


@pytest.mark.parametrize("temporary", [False, True])
def test_ordinary_correction_preserves_short_layer_lifetime_and_scope(store, clock, temporary):
    original = observe(store, scope="geometry", temporary=temporary)
    clock.advance(3)
    corrected = store.correct(original.memory_id, "先给图例再画图")
    assert corrected.layer == "short_term" and corrected.temporary is temporary
    assert corrected.expires_at == original.expires_at and corrected.confirmed_at is None
    assert corrected.scope == "geometry" and corrected.independent_sessions == 1
    assert values(store.snapshot("s1", "geometry")) == {"视觉偏好": "先给图例再画图"}
    assert bool(store.snapshot("other", "geometry")["entries"]) is not temporary
    clock.advance(11)
    assert not store.snapshot("s1", "geometry")["entries"]


def test_corrected_current_exception_does_not_replace_deep_default(store):
    stable = observe(store, session="default")
    store.confirm(stable.memory_id)
    exception = observe(store, session="s2", source="exception", value="先公式", temporary=True)
    corrected = store.correct(exception.memory_id, "只看公式")
    assert values(store.snapshot("s2")) == {"视觉偏好": "只看公式"}
    assert values(store.snapshot("other")) == {"视觉偏好": "先画图"}
    assert stable.memory_id in {entry.memory_id for entry in store.list_entries()}
    # Later user statements still supersede an edit even when timestamps tie.
    observe(store, session="s2", source="reaffirm")
    assert values(store.snapshot("s2")) == {"视觉偏好": "先画图"}
    assert corrected.layer == "short_term"


def test_new_observation_wins_after_more_than_one_hundred_corrections(store):
    stable = observe(store, session="default")
    store.confirm(stable.memory_id)
    corrected = observe(store, session="s2", source="exception", value="先公式", temporary=True)
    # Reproduce the former mixed sequencing bug: source_count * 100 + index
    # would place the next observation before 105 MAX(last_order) + 1 edits.
    for index in range(105):
        corrected = store.correct(corrected.memory_id, f"公式版本{index}")
    with sqlite3.connect(store.path) as db:
        previous_order = db.execute("SELECT MAX(last_order) FROM sessions").fetchone()[0]
    observe(store, session="s2", source="new-user-statement")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT MAX(last_order) FROM sessions").fetchone()[0] > previous_order
    assert values(store.snapshot("s2")) == {"视觉偏好": "先画图"}


def test_explicit_long_term_correction_promotes_and_clears_temporary_expiry(store):
    original = observe(store, temporary=True)
    corrected = store.correct(original.memory_id, "以后先画图", confirm_long_term=True)
    assert corrected.layer == "deep" and not corrected.temporary
    assert corrected.confirmed_at and corrected.expires_at is None
    assert values(store.snapshot("other")) == {"视觉偏好": "以后先画图"}


@pytest.mark.parametrize("confirm_long_term", [False, True])
@pytest.mark.parametrize("state", ["expired", "superseded"])
def test_cannot_correct_inactive_memory(store, clock, state, confirm_long_term):
    original = observe(store)
    if state == "expired":
        clock.advance(14)
    else:
        with store.database.transaction() as db:
            original.state = "superseded"
            store._save(db, original)
    with pytest.raises(ContentError, match="过期或已替代"):
        store.correct(original.memory_id, "新值", confirm_long_term=confirm_long_term)


def test_capacity_prefers_current_temporary_requirement_over_unrelated_deep_default(
    tmp_path, clock
):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_snapshot_entries=1), clock)
    stable = observe(store, field="a", session="old")
    store.confirm(stable.memory_id)
    observe(store, source="current", field="z", value="这次快一点", session="now", temporary=True)
    assert values(store.snapshot("now")) == {"z": "这次快一点"}
    assert values(store.snapshot("other")) == {"a": "先画图"}


def test_oversized_field_winner_cannot_revive_shorter_conflicting_default(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_snapshot_chars=500), clock)
    stable = observe(store, field="style", value="旧默认", session="old")
    store.confirm(stable.memory_id)
    observe(store, source="current", field="style", value="新" * 300, session="now", temporary=True)
    observe(store, source="background", field="background", value="本科生", session="old")
    snapshot = store.snapshot("now")
    assert values(snapshot) == {"background": "本科生"}
    assert len(json.dumps(snapshot, ensure_ascii=False)) <= 500
