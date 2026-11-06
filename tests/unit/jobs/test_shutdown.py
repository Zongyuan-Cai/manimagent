from manimagent.jobs.manager import JobManager
from manimagent.schemas import Generation, Job, TextGenerationRequest
from tests.support.pipeline import settings_for


def test_shutdown_updates_both_job_and_generation_stages(tmp_path):
    queued = JobManager(settings_for(tmp_path / "queued"))
    job = queued.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "a=3"}],
                "instruction": "q",
            }
        ),
    )
    queued.stop()
    result = queued.store.load("jobs", job.job_id, Job)
    generation = queued.store.load("generations", job.resource_ref.id, Generation)
    assert result.status == "interrupted" and result.error.code == "interrupted"
    assert all(s.status == "interrupted" for s in generation.stages.values())


def test_shutdown_continues_draining_after_storage_and_reconciliation_failures(
    tmp_path, monkeypatch
):
    settings = settings_for(tmp_path)
    manager = JobManager(settings)
    request = TextGenerationRequest(
        source={
            "type": "inputs",
            "inputs": [{"type": "text", "text": "x=1"}],
            "instruction": "请详细讲解",
        },
        memory={"session_id": "shutdown"},
    )
    first = manager.submit("generation", request)
    second = manager.submit("generation", request)
    original_save, original_reconcile = manager.store.save, manager.learning_worker.reconcile

    def fail_first_save(kind, identifier, body):
        if kind == "jobs" and identifier == first.job_id:
            raise OSError("controlled shutdown save failure")
        return original_save(kind, identifier, body)

    def fail_first_reconcile(identifier):
        if identifier == first.job_id:
            raise OSError("controlled shutdown reconciliation failure")
        return original_reconcile(identifier)

    with monkeypatch.context() as faults:
        faults.setattr(manager.store, "save", fail_first_save)
        faults.setattr(manager.learning_worker, "reconcile", fail_first_reconcile)
        manager.stop()
    assert manager.queue.unfinished_tasks == 0 and manager.queue.empty()
    for _ in range(settings.max_waiting + 1):
        assert manager.slots.acquire(False)
    assert not manager.slots.acquire(False)
    assert manager.store.load("jobs", first.job_id, Job).status == "queued"
    assert manager.store.load("jobs", second.job_id, Job).status == "interrupted"
    cancelled = manager.learning.get(second.job_id)
    assert cancelled["state"] == "cancelled"
    assert cancelled["error"]["code"] == "answer_not_published"
    # The first disk write can be recovered after storage becomes available.
    restarted = JobManager(settings)
    try:
        restarted.start()
        assert restarted.store.load("jobs", first.job_id, Job).status == "interrupted"
        assert restarted.learning.get(first.job_id)["state"] == "cancelled"
    finally:
        restarted.stop()
