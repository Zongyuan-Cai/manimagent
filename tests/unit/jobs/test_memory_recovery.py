"""Restart recovers a validated interpretation without re-running its model."""

import time

from manimagent.jobs.manager import JobManager
from manimagent.personalization import Interpretation, MemoryProposal, RequestIntent
from manimagent.schemas import Job, TextGenerationRequest
from tests.support.pipeline import settings_for


def pending_interpretation(manager):
    job = manager.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "instruction": "请详细讲解",
                "inputs": [{"type": "text", "text": "x=1"}],
            },
            memory={"session_id": "recovery"},
        ),
    )
    record = manager.store.load_record(job.job_id)
    interpretation = Interpretation(
        intent=RequestIntent(overrides=(), exclude_memory=(), issues=()),
        proposals=(
            MemoryProposal(
                field="explanation.detail", value="detailed", quote="请详细讲解", intent="default"
            ),
        ),
    )
    record["interpretation"] = interpretation.model_dump(mode="json")
    record["memory_save"] = {"state": "pending"}
    manager.store.save("records", job.job_id, record)
    return job, record, interpretation


def test_restart_recovers_pending_interpretation_without_llm(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    original = JobManager(settings)
    job, record, _ = pending_interpretation(original)

    def unexpected_model(*args, **kwargs):
        raise AssertionError("Recovery must not call the interpreter")

    monkeypatch.setattr("manimagent.jobs.execution.interpret_request", unexpected_model)
    restarted = JobManager(settings)
    try:
        restarted.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = restarted.learning.get(job.job_id)
            if task["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert task["state"] == "completed"
        assert task["usage"] == {}
        assert restarted.memory.list_entries()[0].value == "detailed"
        assert restarted.store.load("jobs", job.job_id, Job).status == "interrupted"
        assert (
            restarted.store.load_record(job.job_id)["memory_acceptance"]
            == record["memory_acceptance"]
        )
    finally:
        restarted.stop()


def test_restart_does_not_revive_cancelled_queue_when_record_was_pending(tmp_path):
    settings = settings_for(tmp_path)
    original = JobManager(settings)
    job, record, interpretation = pending_interpretation(original)
    accepted = record["memory_acceptance"]
    original.learning.enqueue(
        job.job_id,
        list(interpretation.proposals),
        accepted["options"],
        accepted["sequence"],
        "请详细讲解",
    )
    original.learning.cancel(job.job_id)
    restarted = JobManager(settings)
    try:
        restarted.start()
        assert restarted.learning.get(job.job_id)["state"] == "cancelled"
        assert not restarted.memory.list_entries()
    finally:
        restarted.stop()
