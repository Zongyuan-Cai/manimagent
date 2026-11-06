"""Background learning boundaries exercised through real API/worker coordination."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ChatProvider
from manimagent.memory.models import MemoryCandidate
from manimagent.schemas import Generation, TextGenerationRequest
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import generate, memory_provider, wait_learning


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def wait(self):
        self.entered.set()
        assert self.release.wait(8), "Test did not release the controlled provider"


def provider_with_hook(settings, captured, hook):
    base = memory_provider(settings, captured)

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        override = hook(body, payload)
        return override if override is not None else base.transport.handler(request)

    return ChatProvider(settings, httpx.MockTransport(handler))


def task_until(client, job_id, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/memory/learning/{job_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if predicate(task):
            return task
        time.sleep(0.01)
    raise AssertionError("Background state/usage did not reach the expected boundary")


def seed_memory(manager, value="简洁"):
    return manager.memory.observe(
        [MemoryCandidate(field="explanation.detail", value=value, evidence=value)],
        session_id="seed",
        source_id="seed",
        source_text=value,
    )[0]


def test_blocked_learning_does_not_delay_published_or_next_answer(tmp_path):
    settings, captured, gate = config(tmp_path), [], Gate()

    def hook(body, payload):
        if "user_text" in payload:
            assert body["max_tokens"] <= 2048 and body["thinking"] == {"type": "disabled"}
            gate.wait()

    app = create_app(settings, provider_with_hook(settings, captured, hook), TemplateRenderer())
    with TestClient(app) as client:
        try:
            job, _, answer = generate(client, memory={"session_id": "first"})
            assert gate.entered.wait(3)
            assert answer["memory_snapshot"]["entries"] == []
            assert job["usage"]["model_calls"] == 2
            busy = client.post(
                "/api/v2/memory/observations",
                json={"text": "请详细讲解", "session_id": "manual", "source_id": "manual_busy"},
            )
            assert busy.status_code == 429
            next_job, _, next_answer = generate(client)
            assert next_job["status"] == "completed" and next_answer["memory_snapshot"] is None
            assert client.get("/api/v2/memory").json()["entries"] == []
        finally:
            gate.release.set()
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "completed" and task["usage"]["model_calls"] == 1
        assert client.get(f"/api/v2/jobs/{job['job_id']}").json()["usage"] == job["usage"]
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer
        assert client.get("/api/v2/memory").json()["entries"][0]["value"] == "详细讲解"


def test_three_independent_sessions_promote_only_after_their_answers(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        answers = []
        for number in range(3):
            job, _, answer = generate(client, memory={"session_id": f"session_{number}"})
            answers.append(answer)
            task = wait_learning(client, job["job_id"])
            assert task["state"] == "completed" and task["attempts"] == 1
            entries = client.get("/api/v2/memory").json()["entries"]
            assert len(entries) == 1 and entries[0]["independent_sessions"] == number + 1
            assert entries[0]["layer"] == ("deep" if number == 2 else "short_term")
        assert answers[0]["memory_snapshot"]["entries"] == []
        assert answers[2]["memory_snapshot"]["entries"][0]["layer"] == "short_term"
        assert task["counts"]["promoted"] == 1
        _, _, later = generate(client, memory={"session_id": "later", "learn": False})
        assert later["memory_snapshot"]["entries"][0]["layer"] == "deep"
        for answer in answers:
            assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


@pytest.mark.parametrize("endpoint", ["answers", "generations"])
def test_legacy_answer_and_nested_material_source_schedule_learning(tmp_path, endpoint):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        _, generation, _ = generate(client)
        source = {
            "material_ids": generation["material_ids"],
            "instruction": "请详细讲解计算方法",
            "memory": {"session_id": "alternate_entry"},
        }
        payload = source if endpoint == "answers" else {"source": {"type": "materials", **source}}
        response = client.post(f"/api/v2/{endpoint}", json=payload)
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed"
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "completed" and task["attempts"] == 1
        assert len(client.get("/api/v2/memory").json()["entries"]) == 1


def test_background_reported_tokens_cannot_exceed_its_independent_limit(tmp_path):
    settings, captured = config(tmp_path), []
    settings.memory_learning_max_tokens = 16000

    def hook(_body, payload):
        if "user_text" in payload:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"candidates":[]}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 15999, "completion_tokens": 100},
                },
            )

    app = create_app(settings, provider_with_hook(settings, captured, hook), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "bounded"})
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "failed"
        assert task["usage"]["input_tokens"] + task["usage"]["output_tokens"] == 16099
        assert task["usage"]["model_calls"] == 1
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


def test_snapshot_is_frozen_when_accepted_even_if_memory_changes_while_queued(tmp_path):
    settings, captured, gate = config(tmp_path), [], Gate()

    def hook(_body, payload):
        if payload.get("instruction") == "hold first" and "candidate_markdown" not in payload:
            gate.wait()

    app = create_app(settings, provider_with_hook(settings, captured, hook), TemplateRenderer())
    original = seed_memory(app.state.job_manager)
    with TestClient(app) as client:
        try:
            first = client.post(
                "/api/v2/generations",
                json={
                    "source": {
                        "type": "inputs",
                        "instruction": "hold first",
                        "inputs": [{"type": "text", "text": "x=1"}],
                    }
                },
            )
            assert first.status_code == 202 and gate.entered.wait(3)
            second = client.post(
                "/api/v2/generations",
                json={
                    "source": {
                        "type": "inputs",
                        "instruction": "frozen second",
                        "inputs": [{"type": "text", "text": "x=1"}],
                    },
                    "memory": {"session_id": "queued", "learn": False},
                },
            )
            assert second.status_code == 202
            correction = client.patch(
                f"/api/v2/memory/{original.memory_id}", json={"value": "先讲结论"}
            )
            assert correction.status_code == 200
        finally:
            gate.release.set()
        job = wait_job(client, second.json()["job_id"])
        assert job["status"] == "completed"
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
        assert answer["memory_snapshot"]["entries"][0]["value"] == "简洁"
        _, _, later = generate(client, memory={"session_id": "later", "learn": False})
        assert later["memory_snapshot"]["entries"][0]["value"] == "先讲结论"


@pytest.mark.parametrize("operation", ["cancel", "correct", "delete", "confirm"])
def test_late_extraction_cannot_override_cancellation_or_manual_changes(tmp_path, operation):
    settings, captured, gate = config(tmp_path), [], Gate()

    def hook(_body, payload):
        if "user_text" in payload:
            gate.wait()

    app = create_app(settings, provider_with_hook(settings, captured, hook), TemplateRenderer())
    original = seed_memory(app.state.job_manager)
    with TestClient(app) as client:
        try:
            job, _, answer = generate(client, memory={"session_id": "new"})
            assert gate.entered.wait(3)
            if operation == "cancel":
                response = client.post(f"/api/v2/memory/learning/{job['job_id']}/cancel")
            elif operation == "correct":
                response = client.patch(
                    f"/api/v2/memory/{original.memory_id}", json={"value": "先讲结论"}
                )
            elif operation == "delete":
                response = client.delete(f"/api/v2/memory/{original.memory_id}")
            else:
                response = client.post(f"/api/v2/memory/{original.memory_id}/confirm")
            assert response.status_code == 200, response.text
            expected = client.get("/api/v2/memory").json()["entries"]
        finally:
            gate.release.set()
        task = task_until(
            client,
            job["job_id"],
            lambda task: (
                task["state"] not in {"waiting", "queued", "running"}
                and task["usage"].get("model_calls") == 1
            ),
        )
        assert task["state"] != "completed"
        assert client.get("/api/v2/memory").json()["entries"] == expected
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


@pytest.mark.parametrize("always_fail", [False, True])
def test_manual_retry_accumulates_usage_and_has_three_attempt_limit(tmp_path, always_fail):
    settings, captured, attempts = config(tmp_path), [], []

    def hook(_body, payload):
        if "user_text" in payload:
            attempts.append(payload)
            if always_fail or len(attempts) == 1:
                return httpx.Response(503, text="controlled failure")

    app = create_app(settings, provider_with_hook(settings, captured, hook), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "retry"})
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "failed" and task["attempts"] == 1
        for count in range(2, 4 if always_fail else 3):
            response = client.post(f"/api/v2/memory/learning/{job['job_id']}/retry")
            assert response.status_code == 200, response.text
            task = wait_learning(client, job["job_id"])
            assert task["attempts"] == count and task["usage"]["model_calls"] == count
        assert task["state"] == ("failed" if always_fail else "completed")
        assert not task["can_retry"]
        assert len(attempts) == (3 if always_fail else 2)
        assert client.post(f"/api/v2/memory/learning/{job['job_id']}/retry").status_code == 409
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer
        entries = client.get("/api/v2/memory").json()["entries"]
        assert len(entries) == (0 if always_fail else 1)
        if entries:
            assert entries[0]["independent_sessions"] == 1 and len(entries[0]["evidence"]) == 1


def test_partial_video_failure_still_learns_but_an_unpublished_answer_does_not(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer(fail=True))
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/generations",
            json={
                "output_mode": "both",
                "source": {
                    "type": "inputs",
                    "instruction": "请详细讲解计算方法",
                    "inputs": [{"type": "text", "text": "x=1"}],
                },
                "memory": {"session_id": "partial"},
            },
        )
        assert response.status_code == 202
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "partial"
        assert wait_learning(client, job["job_id"])["state"] == "completed"

    failed_settings, failed_calls = config(tmp_path / "failed"), []

    def fail_answer(_body, payload):
        if "user_text" not in payload:
            return httpx.Response(401)

    failed_app = create_app(
        failed_settings, provider_with_hook(failed_settings, failed_calls, fail_answer)
    )
    with TestClient(failed_app) as client:
        response = client.post(
            "/api/v2/generations",
            json={
                "source": {
                    "type": "inputs",
                    "instruction": "请详细讲解计算方法",
                    "inputs": [{"type": "text", "text": "x=1"}],
                },
                "memory": {"session_id": "no_answer"},
            },
        )
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "failed"
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "cancelled" and task["attempts"] == 0
        assert not any("user_text" in payload for _, payload in failed_calls)


@pytest.mark.parametrize("published", [False, True])
def test_restart_reconciles_waiting_event_with_recovered_parent(tmp_path, published):
    settings, captured = config(tmp_path), []
    with TestClient(create_app(settings, memory_provider(settings, captured))) as client:
        _, _, saved_answer = generate(client)
    manager = JobManager(settings)
    pending = manager.submit(
        "generation",
        TextGenerationRequest(
            output_mode="both",
            source={
                "type": "inputs",
                "instruction": "请详细讲解计算方法",
                "inputs": [{"type": "text", "text": "x=1"}],
            },
            memory={"session_id": "restart"},
        ),
    )
    assert manager.learning.get(pending.job_id)["state"] == "waiting"
    if published:
        generation = manager.store.load("generations", pending.resource_ref.id, Generation)
        generation.answer_id = saved_answer["answer_id"]
        manager.store.save("generations", generation.generation_id, generation)
    captured.clear()
    with TestClient(create_app(settings, memory_provider(settings, captured))) as client:
        task = wait_learning(client, pending.job_id)
        assert task["state"] == ("completed" if published else "cancelled")
        assert task["attempts"] == int(published)
        parent = client.get(f"/api/v2/jobs/{pending.job_id}").json()
        assert parent["status"] == ("partial" if published else "interrupted")
