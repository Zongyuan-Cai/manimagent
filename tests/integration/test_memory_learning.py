"""Real worker coordination: interpretation, independent commits, retries and fences."""

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import generate, memory_provider, observe, wait_learning


def submit(client, instruction="请详细讲解", **options):
    response = client.post(
        "/api/v2/generations",
        json={
            "output_mode": options.pop("mode", "text"),
            "source": {
                "type": "inputs",
                "instruction": instruction,
                "inputs": [{"type": "text", "text": "x=1"}],
            },
            "memory": {"session_id": "session", **options},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def test_three_sessions_promote_but_current_snapshot_never_changes(tmp_path):
    settings, calls = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, calls), TemplateRenderer())
    with TestClient(app) as client:
        answers = []
        for number in range(3):
            job, _, answer = generate(client, memory={"session_id": f"session_{number}"})
            answers.append(answer)
            task = wait_learning(client, job["job_id"])
            assert task["state"] == "completed" and not task["usage"].get("model_calls", 0)
            entries = client.get("/api/v2/memory").json()["entries"]
            assert len(entries) == 1 and entries[0]["independent_sessions"] == number + 1
            assert entries[0]["layer"] == ("deep" if number == 2 else "short_term")
        assert answers[0]["memory_snapshot"]["entries"] == []
        assert answers[2]["memory_snapshot"]["entries"][0]["layer"] == "short_term"
        for answer in answers:
            assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


def test_answer_failure_and_unsupported_video_do_not_discard_legal_default(tmp_path):
    settings, calls = config(tmp_path), []
    base = memory_provider(settings, calls)

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        if "user_text" not in payload:
            return httpx.Response(401)
        return base.transport.handler(request)

    with TestClient(
        create_app(settings, ChatProvider(settings, httpx.MockTransport(handler)))
    ) as client:
        identifier = submit(client, "以后请详细讲解")
        assert wait_job(client, identifier)["status"] == "failed"
        assert wait_learning(client, identifier)["state"] == "completed"
        entry = client.get("/api/v2/memory").json()["entries"][0]
        assert entry["value"] == "detailed" and entry["layer"] == "deep"
    settings = config(tmp_path / "unsupported")
    with TestClient(
        create_app(settings, memory_provider(settings, []), TemplateRenderer())
    ) as client:
        identifier = submit(client, "以后蓝金，做三维视频", mode="both")
        job = wait_job(client, identifier)
        assert job["status"] == "partial" and job["error"]["code"] == "unsupported_request"
        assert wait_learning(client, identifier)["state"] == "completed"
        assert client.get("/api/v2/memory").json()["entries"][0]["value"] == "blue_gold"


def test_blocked_commit_does_not_block_answer_and_cancellation_is_atomic(tmp_path, monkeypatch):
    settings = config(tmp_path)
    app = create_app(settings, memory_provider(settings, []), TemplateRenderer())
    manager = app.state.job_manager
    entered, release = threading.Event(), threading.Event()
    original = manager.memory.observe_proposals

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager.memory, "observe_proposals", blocked)
    with TestClient(app) as client:
        try:
            job, _, answer = generate(client, memory={"session_id": "blocked"})
            assert entered.wait(3)
            assert job["status"] == "completed"
            assert (
                client.post(f"/api/v2/memory/learning/{job['job_id']}/cancel").json()["state"]
                == "cancelled"
            )
            _, _, next_answer = generate(client)
            assert next_answer["memory_snapshot"] is None
        finally:
            release.set()
        assert wait_learning(client, job["job_id"])["state"] == "cancelled"
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


def test_commit_retry_uses_persisted_candidates_without_another_model_call(tmp_path, monkeypatch):
    settings, calls = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, calls), TemplateRenderer())
    memory = app.state.job_manager.memory
    original, attempts = memory.observe_proposals, []

    def fail_once(*args, **kwargs):
        attempts.append(kwargs["sequence"])
        if len(attempts) == 1:
            raise ContentError("memory_internal_error", "test failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(memory, "observe_proposals", fail_once)
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "retry"})
        assert wait_learning(client, job["job_id"])["state"] == "failed"
        calls_before = len(calls)
        assert client.post(f"/api/v2/memory/learning/{job['job_id']}/retry").status_code == 200
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "completed" and task["attempts"] == 2
        assert len(calls) == calls_before and attempts[0] == attempts[1]
        entry = client.get("/api/v2/memory").json()["entries"][0]
        assert entry["independent_sessions"] == 1
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


def test_acceptance_snapshot_precedes_interpretation_and_late_correction(tmp_path):
    settings, calls = config(tmp_path), []
    base = memory_provider(settings, calls)
    entered, release = threading.Event(), threading.Event()

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        if payload.get("user_text") == "解题":
            entered.set()
            assert release.wait(8)
        return base.transport.handler(request)

    app = create_app(
        settings, ChatProvider(settings, httpx.MockTransport(handler)), TemplateRenderer()
    )
    original = observe(
        app.state.job_manager.memory, field="explanation.detail", value="concise", intent="default"
    )
    with TestClient(app) as client:
        try:
            identifier = submit(client, "解题", learn=False)
            assert entered.wait(3)
            assert (
                client.patch(
                    f"/api/v2/memory/{original.memory_id}",
                    json={"value": "detailed", "confirm_long_term": True},
                ).status_code
                == 200
            )
        finally:
            release.set()
        job = wait_job(client, identifier)
        assert job["status"] == "completed", job
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
        assert answer["effective_profile"]["teaching"]["detail"] == "concise"
        assert answer["memory_snapshot"]["entries"][0]["value"] == "concise"


@pytest.mark.parametrize("endpoint", ["answers", "generations"])
def test_direct_and_nested_material_entry_points_interpret_once(tmp_path, endpoint):
    settings, calls = config(tmp_path), []
    with TestClient(
        create_app(settings, memory_provider(settings, calls), TemplateRenderer())
    ) as client:
        _, generation, _ = generate(client)
        source = {
            "material_ids": generation["material_ids"],
            "instruction": "请详细讲解",
            "memory": {"session_id": "alternate"},
        }
        payload = source if endpoint == "answers" else {"source": {"type": "materials", **source}}
        before = sum("user_text" in p for _, p in calls)
        response = client.post(f"/api/v2/{endpoint}", json=payload)
        assert response.status_code == 202
        identifier = response.json()["job_id"]
        assert wait_job(client, identifier)["status"] == "completed"
        assert wait_learning(client, identifier)["state"] == "completed"
        assert sum("user_text" in p for _, p in calls) == before + 1
