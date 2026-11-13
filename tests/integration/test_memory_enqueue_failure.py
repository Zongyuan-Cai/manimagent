"""A failed independent enqueue stays visible and retries its saved interpretation."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from manimagent.answers.service import AnswerService
from manimagent.api import create_app
from manimagent.errors import ContentError
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import memory_provider, wait_learning


def test_pruned_cancelled_task_cannot_be_requeued_from_stale_failed_record(tmp_path):
    from manimagent.jobs.manager import JobManager
    from manimagent.memory.models import MemoryOptions
    from manimagent.personalization import Interpretation, MemoryProposal, RequestIntent
    from manimagent.schemas import TextGenerationRequest

    manager = JobManager(config(tmp_path))
    request = TextGenerationRequest(
        source={
            "type": "inputs",
            "instruction": "以后蓝金",
            "inputs": [{"type": "text", "text": "x=1"}],
        },
        memory={"session_id": "cancelled"},
    )
    job = manager.submit("generation", request)
    record = manager.store.load_record(job.job_id)
    interpretation = Interpretation(
        intent=RequestIntent(overrides=(), exclude_memory=(), issues=()),
        proposals=(
            MemoryProposal(
                field="visual.palette", value="blue_gold", quote="以后蓝金", intent="default"
            ),
        ),
    )
    record["interpretation"] = interpretation.model_dump(mode="json")
    record["memory_save"] = {"state": "failed"}
    job.memory_save = record["memory_save"]
    manager.store.save("records", job.job_id, record)
    manager.store.save("jobs", job.job_id, job)
    manager.learning.enqueue(
        job.job_id,
        list(interpretation.proposals),
        MemoryOptions(session_id="cancelled"),
        record["memory_acceptance"]["sequence"],
        "以后蓝金",
    )
    assert manager.learning.cancel(job.job_id)["state"] == "cancelled"
    with manager.memory.database.transaction() as db:
        db.execute("DELETE FROM memory_learning_jobs WHERE job_id=?", (job.job_id,))
    status = manager.memory_learning_status(job.job_id)
    assert status["state"] == "cancelled" and not status["can_retry"]
    with pytest.raises(ContentError):
        manager.retry_memory_save(job.job_id)
    assert manager.memory.list_entries() == []
    manager.stop()


def test_enqueue_and_generation_failures_are_separate_and_saved_candidates_retry_without_a_model(
    tmp_path, monkeypatch
):
    settings, calls = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, calls), TemplateRenderer())
    manager = app.state.job_manager
    real_enqueue = manager.learning.enqueue

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("test storage unavailable")

    def bad_answer(*args, **kwargs):
        raise ContentError("content_uncertain", "受控生成失败", state="needs_review")

    monkeypatch.setattr(manager.learning, "enqueue", unavailable)
    monkeypatch.setattr(AnswerService, "generate", bad_answer)
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/generations",
            json={
                "source": {
                    "type": "inputs",
                    "instruction": "以后蓝金",
                    "inputs": [{"type": "text", "text": "x=1"}],
                },
                "memory": {"session_id": "s1", "read": False, "learn": True},
            },
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["job_id"]
        job = wait_job(client, identifier)
        assert job["status"] == "needs_review"
        assert job["memory_save"]["state"] == "failed" and job["memory_save"]["can_retry"]
        task = client.get(f"/api/v2/memory/learning/{identifier}").json()
        assert task["state"] == "failed" and task["error"]["code"] == "memory_commit_unavailable"
        listing = client.get("/api/v2/memory").json()
        assert listing["learning"]["counts"]["failed"] == 1
        assert listing["learning"]["tasks"][0]["job_id"] == identifier
        assert listing["learning"]["unqueued_scan"]["examined"] == 1
        assert listing["entries"] == []
        failed_again = client.post(f"/api/v2/memory/learning/{identifier}/retry", json={})
        assert failed_again.status_code == 503
        assert client.get(f"/api/v2/jobs/{identifier}").json()["memory_save"]["state"] == "failed"
        before = len(calls)
        monkeypatch.setattr(manager.learning, "enqueue", real_enqueue)
        retry = client.post(f"/api/v2/memory/learning/{identifier}/retry", json={})
        assert retry.status_code == 200, retry.text
        saved = wait_learning(client, identifier)
        assert saved["state"] == "completed" and len(calls) == before
        entries = client.get("/api/v2/memory").json()["entries"]
        assert len(entries) == 1 and entries[0]["value"] == "blue_gold"
        current = client.get(f"/api/v2/jobs/{identifier}").json()
        assert (
            current["status"] == "needs_review" and current["memory_save"]["state"] == "completed"
        )
        assert (
            client.post(f"/api/v2/memory/learning/{identifier}/retry", json={}).status_code == 409
        )
        # Queue-history retention cannot make a previously finished generation unreadable.
        with manager.learning.database.transaction() as db:
            db.execute("DELETE FROM memory_learning_jobs WHERE job_id=?", (identifier,))
        historic = client.get(f"/api/v2/jobs/{identifier}")
        assert historic.status_code == 200 and historic.json()["memory_save"]["state"] == "unknown"
