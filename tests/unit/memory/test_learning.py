"""The queue commits validated candidates without a second model call."""

import pytest

from manimagent.errors import ContentError
from manimagent.memory.learning import LearningStore
from manimagent.memory.store import MemoryStore
from manimagent.schemas import Usage
from tests.support.memory import pending


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "memory.sqlite3"
    return MemoryStore(path), LearningStore(path)


def enqueue(memory, queue, source="job1"):
    task = pending(memory, source=source, intent="default")
    queue.enqueue(
        source, task["proposals"], {"session_id": "s1"}, task["sequence"], task["source_text"]
    )
    return task


def test_immediately_queued_no_answer_publication_or_second_model(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    assert queue.get("job1")["state"] == "queued"
    task = queue.claim()
    assert task["proposals"][0]["value"] == "detailed" and task["sequence"] == original["sequence"]
    memory.observe_proposals(**original, learning_job_id="job1")
    result = queue.finish("job1", "completed", usage=Usage())
    assert result["state"] == "completed" and result["counts"] == {
        "created": 1,
        "reinforced": 0,
        "promoted": 1,
    }
    assert result["usage"] == {} and result["usage_unknown"] is False


def test_cancel_before_commit_fences_write(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    assert queue.cancel("job1")["state"] == "cancelled"
    with pytest.raises(ContentError, match="已停止"):
        memory.observe_proposals(**original, learning_job_id="job1")
    assert not memory.list_entries()


def test_cancel_after_commit_returns_actual_completed_result(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    memory.observe_proposals(**original, learning_job_id="job1")
    assert queue.cancel("job1")["state"] == "completed"


def test_recover_committed_receipt_after_crash(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    memory.observe_proposals(**original, learning_job_id="job1")
    assert LearningStore(queue.path).recover() == {"completed": 1, "failed": 0}
    assert queue.get("job1")["counts_known"]
    assert memory.list_entries()[0].independent_sessions == 1


def test_recover_uncommitted_retry_keeps_original_sequence(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    assert queue.recover() == {"completed": 0, "failed": 1}
    queue.retry("job1", 99999)
    assert queue.claim()["sequence"] == original["sequence"]
    memory.observe_proposals(**original, learning_job_id="job1")
    assert queue.finish("job1", "completed")["attempts"] == 2


def test_cannot_finish_without_committing_or_report_fake_model_usage(stores):
    memory, queue = stores
    enqueue(memory, queue)
    queue.claim()
    with pytest.raises(ContentError, match="尚未提交"):
        queue.finish("job1", "completed")
    with pytest.raises(ContentError, match="模型调用"):
        queue.finish("job1", "failed", usage=Usage(model_calls=1))


def test_queue_idempotent_and_rejects_rebinding(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    enqueue(memory, queue)
    assert queue.list()["counts"]["queued"] == 1
    with pytest.raises(ContentError, match="改绑"):
        queue.enqueue(
            "job1", [], {"session_id": "s1"}, original["sequence"], original["source_text"]
        )


def test_memory_failure_does_not_require_generation_status(stores):
    memory, queue = stores
    enqueue(memory, queue)
    queue.claim()
    result = queue.finish("job1", "failed", error={"code": "memory_internal_error"})
    assert result["can_retry"] and result["error"]["code"] == "memory_internal_error"


def test_recreated_pruned_queue_task_replays_without_losing_receipt(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    first = memory.observe_proposals(**original, learning_job_id="job1")[0]
    queue.finish("job1", "completed")
    with queue.database.transaction() as db:
        db.execute("DELETE FROM memory_learning_jobs WHERE job_id='job1'")
    enqueue(memory, queue)
    queue.claim()
    second = memory.observe_proposals(**original, learning_job_id="job1")[0]
    result = queue.finish("job1", "completed")
    assert first.memory_id == second.memory_id
    assert result["counts"] == {"created": 0, "reinforced": 0, "promoted": 0}
    assert second.independent_sessions == 1


def test_cancelled_source_cannot_reenqueue_after_history_pruning(stores, monkeypatch):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    queue.finish("job1", "failed", error={"code": "memory_internal_error"})
    queue.retry("job1")
    queue.cancel("job1")
    assert memory.was_withdrawn("job1")
    monkeypatch.setattr("manimagent.memory.learning.MAX_HISTORY", 0)
    assert queue.cleanup()["removed"] == 1
    with pytest.raises(ContentError) as error:
        enqueue(memory, queue)
    assert error.value.error.code == "memory_learning_cancelled"
    assert queue.claim() is None
    assert memory.observe_proposals(**original) == []
    assert not memory.list_entries()


def test_withdrawn_source_cannot_enter_or_retry_queue(stores):
    memory, queue = stores
    original = pending(memory, source="withdraw-before-enqueue")
    memory.withdraw_source("withdraw-before-enqueue")
    with pytest.raises(ContentError) as error:
        queue.enqueue(
            "withdraw-before-enqueue",
            original["proposals"],
            {"session_id": "s1"},
            original["sequence"],
            original["source_text"],
        )
    assert error.value.error.code == "memory_learning_cancelled"
    enqueue(memory, queue)
    queue.claim()
    queue.finish("job1", "failed", error={"code": "memory_internal_error"})
    memory.withdraw_source("job1")
    assert queue.retry("job1")["state"] == "cancelled"
    assert queue.claim() is None
    assert not memory.list_entries()


def test_cancel_after_atomic_commit_does_not_withdraw_its_evidence(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    memory.observe_proposals(**original, learning_job_id="job1")
    assert queue.cancel("job1")["state"] == "completed"
    assert not memory.was_withdrawn("job1")
    assert memory.list_entries()[0].independent_sessions == 1


def test_reenqueue_preserves_committed_result_even_if_source_was_later_withdrawn(stores):
    memory, queue = stores
    original = enqueue(memory, queue)
    queue.claim()
    memory.observe_proposals(**original, learning_job_id="job1")
    memory.withdraw_source("job1")
    queue.enqueue(
        "job1",
        original["proposals"],
        {"session_id": "s1"},
        original["sequence"],
        original["source_text"],
    )
    assert queue.get("job1")["state"] == "completed"
    assert not memory.list_entries()
