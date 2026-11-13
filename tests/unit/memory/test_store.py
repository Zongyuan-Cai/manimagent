"""State transitions are grounded, ordered and independent of generated artifacts."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from manimagent.errors import ContentError
from manimagent.memory.models import MemoryOptions, MemoryPolicy
from manimagent.memory.store import MemoryStore
from manimagent.personalization import MemoryProposal
from tests.support.memory import Clock, pending
from tests.support.memory import observe_proposals as observe


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return MemoryStore(tmp_path / "memory.sqlite3", clock=clock)


def test_quote_separate_from_canonical_identity_and_session_support(store):
    one = observe(store)[0]
    observe(store, source="r2", quote="每一步都解释清楚")
    two = observe(store, source="r3", session="s2")[0]
    assert (
        two.memory_id == one.memory_id
        and two.independent_sessions == 2
        and two.layer == "short_term"
    )
    assert {e.quote for e in two.evidence} == {"请详细讲解", "每一步都解释清楚"}
    three = observe(store, source="r4", session="s3")[0]
    assert three.layer == "deep" and three.confirmed_at is None


def test_explicit_default_and_correction_do_not_copy_old_support(store):
    for i in range(3):
        observe(store, source=f"r{i}", session=f"s{i}")
    old = store.list_entries()[0]
    new = store.correct(old.memory_id, "concise")
    assert new.layer == "deep" and new.independent_sessions == 1
    assert new.evidence[0].kind == "correction"
    assert [e.value for e in store.list_entries()] == ["concise"]


def test_acceptance_freezes_before_new_write_and_read_off(store):
    old = observe(store, intent="default")[0]
    task = pending(store, source="new", value="concise", intent="default")
    snap = store.accept("new", "s1", "请详细讲解")["snapshot"]
    store.observe_proposals(**task)
    assert snap["entries"][0]["value"] == old.value
    assert store.accept("disabled", "s1", "请详细讲解", read=False)["snapshot"] is None
    assert MemoryOptions(session_id="s", read=False, learn=True).learn


def test_out_of_order_explicit_defaults_use_acceptance_order(store):
    early = pending(store, source="early", value="concise", intent="default")
    late = pending(store, source="late", value="detailed", intent="default")
    store.observe_proposals(**late)
    assert store.observe_proposals(**early) == []
    assert [e.value for e in store.list_entries()] == ["detailed"]


def test_later_accepted_default_survives_earlier_late_commit(store):
    early = pending(store, source="early", value="concise", intent="default")
    late = pending(store, source="late", value="detailed", intent="default")
    store.observe_proposals(**early)
    store.observe_proposals(**late)
    assert [e.value for e in store.list_entries()] == ["detailed"]


def test_delete_blocks_replay_new_source_and_default_until_restore(store):
    original = pending(store)
    entry = store.observe_proposals(**original)[0]
    delayed = pending(store, source="delayed", intent="default")
    store.delete(entry.memory_id)
    assert store.observe_proposals(**original) == []
    assert store.observe_proposals(**delayed) == []
    assert observe(store, source="new-source") == []
    assert observe(store, source="new-default", intent="default") == []
    restored = observe(store, source="restore", intent="restore")[0]
    assert restored.layer == "deep" and restored.independent_sessions == 1


def test_second_forget_fences_an_intermediate_restore(store):
    e = observe(store, intent="default")[0]
    store.delete(e.memory_id)
    restore = pending(store, source="restore", intent="restore")
    observe(store, source="forget-again", intent="forget")
    assert store.observe_proposals(**restore) == []


def test_withdraw_recalculates_real_support_and_preserves_confirmed_basis(store):
    for i in range(3):
        observe(store, source=f"r{i}", session=f"s{i}")
    store.withdraw_source("r0")
    e = store.list_entries()[0]
    assert e.layer == "short_term" and e.independent_sessions == 2
    store.confirm(e.memory_id)
    store.withdraw_source("r1")
    e = store.list_entries()[0]
    assert e.layer == "deep" and e.confirmed_at


def test_withdraw_before_commit_prevents_delayed_source(store):
    task = pending(store)
    store.withdraw_source("r1")
    assert store.observe_proposals(**task) == [] and not store.list_entries()


def test_evidence_display_limit_does_not_limit_withdrawal_accounting(tmp_path, clock):
    store = MemoryStore(tmp_path / "m.db", MemoryPolicy(max_evidence_per_entry=1), clock)
    for i in range(3):
        observe(store, source=f"r{i}", session=f"s{i}")
    assert len(store.list_entries()[0].evidence) == 1
    store.withdraw_source("r0")
    assert store.list_entries()[0].independent_sessions == 2


def test_recent_expires_but_stable_does_not(store, clock):
    observe(store)
    observe(store, source="stable", field="visual.palette", value="blue_gold", intent="default")
    clock.advance(14)
    assert [e.field for e in store.list_entries()] == ["visual.palette"]
    assert store.cleanup()["expired"] == 1
    clock.advance(1000)
    assert store.snapshot("later")["entries"][0]["value"] == "blue_gold"


def test_self_reported_background_does_not_auto_promote_forever(store, clock):
    for i in range(3):
        observe(
            store, source=f"r{i}", session=f"s{i}", field="profile.background", value="first_year"
        )
    e = store.list_entries()[0]
    assert e.self_reported and e.layer == "short_term" and e.expires_at
    store.confirm(e.memory_id)
    clock.advance(365)
    assert not store.snapshot("later")["entries"]


def test_all_scope_candidates_preserved_for_compiler(store):
    observe(store, source="global", intent="default")
    observe(store, source="course", scope="course.analysis", value="concise", intent="default")
    observe(store, source="topic", scope="topic.analysis.limits", value="detailed")
    assert len(store.snapshot("new", "topic.analysis.limits")["entries"]) == 3
    assert len(store.snapshot("new", "course.other")["entries"]) == 1


def test_invalid_contract_quote_and_scope_do_not_write(store):
    with pytest.raises(ValidationError):
        MemoryProposal(field="unknown", value="x", quote="x")
    with pytest.raises(ValidationError):
        MemoryOptions(session_id="s", scope="geometry")
    task = pending(store)
    task["proposals"][0] = MemoryProposal(
        field="explanation.detail", value="detailed", quote="捏造原句"
    )
    with pytest.raises(ContentError, match="逐字"):
        store.observe_proposals(**task)
    assert not store.list_entries()


def test_snapshot_budget_reports_instead_of_dropping_candidates(tmp_path, clock):
    store = MemoryStore(tmp_path / "m.db", MemoryPolicy(max_snapshot_entries=1), clock)
    observe(store, read=False)
    observe(store, source="second", field="visual.palette", value="blue_gold", read=False)
    with pytest.raises(ContentError, match="未静默裁剪"):
        store.snapshot("s")


def test_source_idempotence_and_conflict(store):
    task = pending(store)
    first = store.observe_proposals(**task)[0]
    again = store.observe_proposals(**task)[0]
    assert first == again
    with pytest.raises(ContentError, match="同一来源"):
        store.accept("r1", "s1", "different")
    assert (
        store.replay(session_id="s1", source_id="r1", source_text="请详细讲解")[0].memory_id
        == first.memory_id
    )


def test_concurrent_acceptances_and_commits_have_unique_order_and_support(tmp_path):
    path = tmp_path / "m.db"
    stores = [MemoryStore(path) for _ in range(4)]

    def run(i):
        task = pending(stores[i % 4], source=f"r{i}", session=f"s{i}")
        stores[i % 4].observe_proposals(**task)
        return task["sequence"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(run, range(8)))
    assert len(set(sequences)) == 8
    # Late evidence for the same stable value is retained without replacing newer defaults.
    entry = stores[0].list_entries()[0]
    assert entry.independent_sessions == 8 and entry.layer == "deep"


def test_capacity_never_discards_confirmed_default(tmp_path, clock):
    store = MemoryStore(tmp_path / "m.db", MemoryPolicy(max_entries=1), clock)
    observe(store, intent="default")
    with pytest.raises(ContentError, match="容量"):
        observe(store, source="second", field="visual.palette", value="blue_gold", intent="default")
    assert len(store.list_entries()) == 1


def test_reading_does_not_increment_support_or_revision(store):
    observe(store)
    revision = store.revision()
    for _ in range(3):
        store.snapshot("new")
    assert store.revision() == revision and store.list_entries()[0].independent_sessions == 1


def test_late_commit_cannot_renew_expired_observation_or_promote_it(store, clock):
    tasks = [pending(store, source=f"old{i}", session=f"s{i}") for i in range(3)]
    clock.advance(30)
    for task in tasks:
        assert store.observe_proposals(**task) == []
    assert not store.list_entries()


def test_promotion_requires_three_sessions_in_one_acceptance_time_window(store, clock):
    observe(store, source="a", session="a")
    clock.advance(10)
    observe(store, source="b", session="b")
    clock.advance(10)
    e = observe(store, source="c", session="c")[0]
    assert e.layer == "short_term" and e.independent_sessions == 2
    e = observe(store, source="d", session="d")[0]
    assert e.layer == "deep"
    clock.advance(100)
    assert store.snapshot("later")["entries"][0]["layer"] == "deep"
    store.withdraw_source("d")
    assert not store.snapshot("later")["entries"]


def test_restore_accepted_before_delete_cannot_break_new_fence(store):
    e = observe(store, intent="default")[0]
    old_restore = pending(store, source="old-restore", intent="restore")
    store.delete(e.memory_id)
    assert store.observe_proposals(**old_restore) == []


def test_forget_removes_original_support_quotes_but_keeps_suppression(store):
    e = observe(store)[0]
    store.delete(e.memory_id)
    with store.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_suppressions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_full_source_ledger_still_allows_forget_and_preserves_revocation(tmp_path, clock):
    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_sources=1), clock)
    original = pending(store, intent="default")
    entry = store.observe_proposals(**original)[0]
    with pytest.raises(ContentError, match="来源索引"):
        store.accept("overflow", "s2", "新来源", read=False)
    previous_revision = store.revision()
    store.delete(entry.memory_id)
    assert store.revision() == previous_revision + 1
    assert not store.list_entries()
    assert store.observe_proposals(**original) == []
    with store.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM acceptances").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        for table in ("memory_suppressions", "memory_explicit_fences", "memory_fences"):
            assert db.execute("SELECT sequence FROM " + table).fetchone()[0] > original["sequence"]
    reopened = MemoryStore(store.path, MemoryPolicy(max_sources=2), clock)
    assert observe(reopened, source="new-id") == []


def test_invalid_management_value_is_reported_without_mutation(store):
    e = observe(store)[0]
    revision = store.revision()
    with pytest.raises(ContentError) as error:
        store.correct(e.memory_id, "arbitrary value")
    assert error.value.http_status == 422 and error.value.error.code == "invalid_memory"
    assert store.revision() == revision and store.list_entries()[0].value == e.value


def test_correct_to_forgotten_value_requires_explicit_restore(store):
    e = observe(store)[0]
    store.delete(e.memory_id)
    replacement = observe(store, source="other", value="concise")[0]
    with pytest.raises(ContentError) as error:
        store.correct(replacement.memory_id, "detailed")
    assert error.value.error.code == "memory_suppressed"
    assert store.list_entries()[0].value == "concise"


def test_old_observation_cannot_cross_forget_then_restore(store):
    old = pending(store, source="old", session="old-session", quote="旧的原句证据")
    e = observe(store, source="new-default", intent="default")[0]
    store.delete(e.memory_id)
    restored = observe(store, source="restore", intent="restore")[0]
    assert store.observe_proposals(**old) == []
    current = store.list_entries()[0]
    assert current.memory_id == restored.memory_id and current.independent_sessions == 1
    assert all(e.quote != "旧的原句证据" for e in current.evidence)


def test_old_observation_cannot_cross_correction_back_to_same_value(store):
    old = pending(store, source="old", session="old-session")
    original = observe(store, source="default", intent="default")[0]
    changed = store.correct(original.memory_id, "concise")
    changed_back = store.correct(changed.memory_id, "detailed")
    assert store.observe_proposals(**old) == []
    assert store.list_entries()[0].memory_id == changed_back.memory_id
    assert store.list_entries()[0].independent_sessions == 1
