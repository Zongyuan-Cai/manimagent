import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from manimagent.errors import ContentError
from manimagent.memory.database import fingerprint
from manimagent.memory.learning import LearningStore
from manimagent.memory.models import MemoryCandidate, MemoryOptions
from manimagent.memory.store import MemoryStore
from manimagent.schemas import Usage
from tests.support.memory import Clock, observe


@pytest.fixture
def queue(tmp_path):
    return LearningStore(tmp_path / "memory.sqlite3")


@pytest.fixture
def memory(queue):
    return MemoryStore(queue.path)


def begin(queue, job_id="job-one", session="s1", text="我希望详细讲解"):
    queue.enqueue(
        job_id, text, MemoryOptions(session_id=session), MemoryStore(queue.path).revision()
    )
    queue.activate(job_id)
    return queue.claim()


def commit(queue, task, candidates=None):
    if candidates is None:
        candidates = [
            MemoryCandidate(field="explanation.detail", value="详细", evidence=task["text"])
        ]
    return MemoryStore(queue.path).observe(
        candidates,
        session_id=task["options"]["session_id"],
        scope=task["options"]["scope"],
        source_id=task["job_id"],
        source_text=task["text"],
        expected_revision=task["revision"],
        learning_job_id=task["job_id"],
    )


def usage(calls=1, tokens=10):
    return Usage(model_calls=calls, input_tokens=tokens, output_tokens=2, tokens_reported=True)


def raw_payload(queue, job_id):
    with sqlite3.connect(queue.path) as db:
        return json.loads(
            db.execute(
                "SELECT payload FROM memory_learning_jobs WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        )


def test_waiting_requires_activation_is_durable_and_never_exposes_input(queue):
    options = MemoryOptions(session_id="private-session")
    first = queue.enqueue("job-one", "private-user-original", options, 0)
    assert first["state"] == "waiting" and queue.claim() is None
    restarted = LearningStore(queue.path)
    assert restarted.enqueue("job-one", "private-user-original", options, 0) == first
    assert restarted.waiting_ids() == ["job-one"]
    for summary in (restarted.get("job-one"), restarted.list()):
        encoded = json.dumps(summary)
        assert "private-user-original" not in encoded and "private-session" not in encoded
        assert (
            '"text"' not in encoded and '"settings"' not in encoded and '"options"' not in encoded
        )
    with pytest.raises(ContentError, match="改绑"):
        restarted.enqueue("job-one", "different", options, 0)
    assert restarted.activate("job-one")["state"] == "queued"
    task = restarted.claim()
    assert task["text"] == "private-user-original" and task["attempt"] == 1
    assert restarted.claim() is None


def test_claim_is_atomic_across_independent_connections(queue):
    queue.enqueue("job-one", "用户表达", {"session_id": "s1"}, 0)
    queue.activate("job-one")
    stores = [LearningStore(queue.path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda store: store.claim(), stores))
    assert sum(item is not None for item in claimed) == 1
    assert queue.get("job-one")["attempts"] == 1


def test_write_receipts_count_creation_reinforcement_and_promotion_atomically(queue):
    first_id = None
    for number in range(3):
        task = begin(queue, job_id=f"job-{number}", session=f"session-{number}")
        result = commit(queue, task)
        first_id = first_id or result[0].memory_id
        finished = queue.finish(task["job_id"], "completed", usage=usage())
        assert finished["state"] == "completed" and finished["counts_known"]
        assert finished["entry_ids"] == [first_id]
        assert finished["counts"] == {
            "created": int(number == 0),
            "reinforced": int(number > 0),
            "promoted": int(number == 2),
        }
        assert finished["promoted_ids"] == ([first_id] if number == 2 else [])
        assert "text" not in raw_payload(queue, task["job_id"])
        assert queue.finish(task["job_id"], "completed", usage=usage())["usage"]["model_calls"] == 1


def test_provider_failures_require_manual_retry_and_accumulate_cost_with_three_attempt_limit(
    queue, memory
):
    task = begin(queue)
    for attempt in range(1, 4):
        assert task["attempt"] == attempt
        failed = queue.finish(
            task["job_id"],
            "failed",
            usage=usage(tokens=attempt * 10),
            error={
                "code": "provider_timeout",
                "message": "remote-private-secret",
                "details": {"text": "private"},
            },
        )
        assert queue.claim() is None
        assert failed["usage"]["model_calls"] == attempt
        assert failed["usage"]["input_tokens"] == sum(10 * n for n in range(1, attempt + 1))
        assert len(failed["attempt_history"]) == attempt
        assert "remote-private-secret" not in json.dumps(failed)
        assert failed["can_retry"] == (attempt < 3)
        if attempt < 3:
            queue.retry(task["job_id"], memory.revision())
            task = queue.claim()
    with pytest.raises(ContentError, match="三次"):
        queue.retry(task["job_id"], memory.revision())
    assert "text" not in raw_payload(queue, task["job_id"])


def test_uncommitted_restart_marks_failure_with_unknown_cost_without_automatic_call(queue):
    begin(queue)
    restored = LearningStore(queue.path)
    assert restored.recover() == {"completed": 0, "failed": 1}
    failed = restored.get("job-one")
    assert failed["can_retry"] and failed["attempts"] == 1 and failed["usage_unknown"]
    assert failed["error"]["code"] == "interrupted" and failed["usage"] == {}
    assert restored.claim() is None
    restored.retry("job-one", 0)
    assert restored.claim()["attempt"] == 2


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_crash_after_commit_recovers_without_relearning_or_resurrecting_deleted_data(
    queue, memory, deleted, empty
):
    task = begin(queue)
    entries = commit(queue, task, [] if empty else None)
    if deleted and entries:
        memory.delete(entries[0].memory_id)
    assert LearningStore(queue.path).recover() == {"completed": 1, "failed": 0}
    recovered = queue.get(task["job_id"])
    assert recovered["counts_known"] and recovered["usage_unknown"]
    assert recovered["entry_ids"] == ([] if empty or deleted else [entries[0].memory_id])
    assert recovered["counts"]["created"] == int(not empty and not deleted)
    assert not recovered["can_retry"] and queue.claim() is None
    assert "text" not in raw_payload(queue, task["job_id"])


def test_cancel_blocks_late_write_but_retains_late_provider_usage_once(queue, memory):
    task = begin(queue)
    assert queue.cancel(task["job_id"])["state"] == "cancelled"
    with pytest.raises(ContentError) as exc:
        commit(queue, task)
    assert exc.value.error.code == "memory_learning_cancelled" and not memory.list_entries()
    for _ in range(2):
        cancelled = queue.finish(
            task["job_id"], "failed", usage=usage(), error={"code": "memory_learning_cancelled"}
        )
        assert cancelled["state"] == "cancelled" and not cancelled["can_retry"]
        assert cancelled["usage"]["model_calls"] == 1 and not cancelled["usage_unknown"]
    assert len(cancelled["attempt_history"]) == 1
    assert cancelled["attempt_history"][0]["state"] == "cancelled"


def test_cancellation_after_atomic_commit_reports_completed_instead_of_false_cancel(queue):
    task = begin(queue)
    commit(queue, task)
    assert queue.cancel(task["job_id"])["state"] == "completed"
    finished = queue.finish(task["job_id"], "completed", usage=usage())
    assert finished["usage"]["model_calls"] == 1 and not finished["usage_unknown"]


@pytest.mark.parametrize("action", ["confirm", "correct", "delete"])
def test_manual_revision_fences_stale_background_writes(queue, memory, action):
    existing = observe(memory)
    captured = memory.snapshot("s1")
    task = begin(queue)
    if action == "correct":
        memory.correct(existing.memory_id, "新值")
    else:
        getattr(memory, action)(existing.memory_id)
    assert memory.revision() == captured["manual_revision"] + 1
    with pytest.raises(ContentError) as exc:
        commit(queue, task)
    assert exc.value.error.code == "memory_changed"
    with sqlite3.connect(queue.path) as db:
        assert (
            db.execute(
                "SELECT 1 FROM sources WHERE id=?", (fingerprint(task["job_id"]),)
            ).fetchone()
            is None
        )
    assert "write_result" not in raw_payload(queue, task["job_id"])


def test_committed_replay_precedes_revision_and_cancel_checks(queue, memory):
    task = begin(queue)
    entries = commit(queue, task)
    memory.delete(entries[0].memory_id)
    # Source replay returns the already-deleted result as empty; it never writes.
    assert commit(queue, task) == []


def test_pending_limit_and_terminal_history_are_bounded_and_waiting_not_hidden(queue):
    for number in range(100):
        queue.enqueue(f"waiting-{number}", "候选表达", {"session_id": "s1"}, 0)
    with pytest.raises(ContentError) as exc:
        queue.enqueue("overflow", "候选表达", {"session_id": "s1"}, 0)
    assert exc.value.error.code == "memory_learning_capacity"
    for number in range(99):
        queue.cancel(f"waiting-{number}")
    for number in range(105):
        job_id = f"terminal-{number}"
        queue.enqueue(job_id, "候选表达", {"session_id": "s1"}, 0)
        queue.cancel(job_id)
    view = queue.list()
    assert len(view["tasks"]) == 20 and view["counts"]["cancelled"] == 200
    assert queue.waiting_ids() == ["waiting-99"]
    assert queue.cleanup()["remaining"] == 201


def test_queue_cleanup_leaves_entry_cleanup_to_configured_memory_store(queue, memory):
    clock = Clock()
    memory.clock = clock
    observe(memory)
    clock.advance(14)
    queue.cleanup()
    assert len(memory.list_entries()) == 1
    assert memory.cleanup()["expired"] == 1
    assert memory.revision() == 0 and not memory.list_entries()


def test_queue_does_not_let_uncommitted_task_claim_success(queue):
    task = begin(queue)
    with pytest.raises(ContentError, match="尚未提交"):
        queue.finish(task["job_id"], "completed", usage=usage(calls=0))


@pytest.mark.parametrize("committed", [False, True])
def test_history_retains_cancelled_inflight_usage_until_response_settles(queue, committed):
    task = begin(queue)
    if committed:
        commit(queue, task)
    queue.cancel(task["job_id"])
    for number in range(200):
        job_id = f"cancel-{number}"
        queue.enqueue(job_id, "用户表达", {"session_id": "s1"}, 0)
        queue.cancel(job_id)
    assert queue.cleanup()["remaining"] == 200
    pending = queue.get(task["job_id"])
    assert pending["usage_unknown"] and "usage_pending" not in pending
    settled = queue.finish(task["job_id"], "failed", usage=usage())
    assert settled["state"] == ("completed" if committed else "cancelled")
    assert settled["usage"]["model_calls"] == 1 and not settled["usage_unknown"]
    assert "usage_pending" not in raw_payload(queue, task["job_id"])


def test_recovery_releases_unsettled_history_without_inventing_usage(queue):
    task = begin(queue)
    queue.cancel(task["job_id"])
    assert raw_payload(queue, task["job_id"])["usage_pending"]
    assert LearningStore(queue.path).recover() == {"completed": 0, "failed": 0}
    recovered = queue.get(task["job_id"])
    assert recovered["state"] == "cancelled" and recovered["usage_unknown"]
    assert recovered["usage"] == {} and not recovered["can_retry"]
    assert "usage_pending" not in raw_payload(queue, task["job_id"])
