"""Typed request/profile/memory integration through the actual API."""

from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import generate, memory_provider, observe, wait_learning


def setup(tmp_path, **kwargs):
    settings, calls = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, calls, **kwargs), TemplateRenderer())
    return app, calls


def test_no_memory_still_interprets_current_requirements_without_saving(tmp_path):
    app, calls = setup(tmp_path)
    with TestClient(app) as client:
        _, _, answer = generate(client, instruction="这次请详细讲解")
        assert answer["memory_snapshot"] is None
        assert answer["effective_profile"]["teaching"]["detail"] == "detailed"
        assert sum("user_text" in p for _, p in calls) == 1
        assert all("user_memory" not in p for _, p in calls)
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get("/api/v2/memory").json()["learning"]["tasks"] == []


def test_single_interpretation_shares_frozen_profile_with_answer_video_and_revision(tmp_path):
    app, calls = setup(tmp_path)
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "one"}, mode="both")
        assert answer["memory_snapshot"]["entries"] == []
        task = wait_learning(client, job["job_id"])
        assert task["state"] == "completed" and not task["usage"].get("model_calls", 0)
        assert sum("user_text" in p for _, p in calls) == 1
        extractor = next(p for _, p in calls if "user_text" in p)
        assert "materials" not in extractor and "虚构人物" not in extractor["user_text"]
        assert job["usage"]["model_calls"] == len(calls)
        assert all("user_memory" not in p for _, p in calls)
        entry = client.get("/api/v2/memory").json()["entries"][0]
        assert entry["value"] == "detailed"
        assert (
            client.patch(
                f"/api/v2/memory/{entry['memory_id']}",
                json={"value": "concise", "confirm_long_term": True},
            ).status_code
            == 200
        )
        revised = client.post(
            "/api/v2/answers/revisions",
            json={"base_answer_id": answer["answer_id"], "markdown": answer["markdown"]},
        )
        assert revised.status_code == 201, revised.text
        assert revised.json()["memory_snapshot"] == answer["memory_snapshot"]
        assert revised.json()["effective_profile"] == answer["effective_profile"]
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer
        assert (
            client.post(
                "/api/v2/generations",
                json={
                    "source": {"type": "answer", "answer_id": answer["answer_id"]},
                    "memory": {"session_id": "new"},
                },
            ).status_code
            == 422
        )


def test_read_and_learn_are_independent_and_temporary_requirement_never_persists(tmp_path):
    app, calls = setup(tmp_path)
    saved = observe(
        app.state.job_manager.memory, field="explanation.detail", value="concise", intent="default"
    )
    with TestClient(app) as client:
        job, _, answer = generate(
            client, memory={"session_id": "learn_only", "read": False, "learn": True}
        )
        assert answer["memory_snapshot"] is None
        assert all(a["origin"] != "memory" for a in answer["effective_profile"]["applications"])
        assert wait_learning(client, job["job_id"])["state"] == "completed"
        before = client.get("/api/v2/memory").json()["entries"]
        job, _, answer = generate(
            client,
            memory={"session_id": "read_only", "read": True, "learn": False},
            instruction="解题",
        )
        assert answer["effective_profile"]["teaching"]["detail"] == "concise"
        assert client.get("/api/v2/memory").json()["entries"] == before
        assert client.get(f"/api/v2/memory/learning/{job['job_id']}").status_code == 404
        job, _, answer = generate(
            client, memory={"session_id": "temporary"}, instruction="这次请详细讲解"
        )
        assert answer["effective_profile"]["teaching"]["detail"] == "detailed"
        assert wait_learning(client, job["job_id"])["state"] == "completed"
        assert client.get("/api/v2/memory").json()["entries"] == before
        assert saved.memory_id in [e["memory_id"] for e in before]


def test_same_request_forget_excludes_frozen_value_before_background_commit(tmp_path):
    app, _ = setup(tmp_path)
    observe(app.state.job_manager.memory, intent="default")
    with TestClient(app) as client:
        job, _, answer = generate(
            client, memory={"session_id": "forget"}, instruction="忘记蓝金并解题"
        )
        assert answer["memory_snapshot"]["entries"][0]["value"] == "blue_gold"
        assert answer["effective_profile"]["visual"]["theme"] == "standard"
        assert wait_learning(client, job["job_id"])["state"] == "completed"
        assert client.get("/api/v2/memory").json()["entries"] == []


def test_management_replay_delete_schema_and_source_withdrawal(tmp_path):
    app, calls = setup(tmp_path)
    payload = {"text": "请详细讲解", "session_id": "one", "source_id": "event_one"}
    with TestClient(app) as client:
        response = client.post("/api/v2/memory/observations", json=payload)
        assert response.status_code == 200, response.text
        entry = response.json()["entries"][0]
        assert (
            client.post("/api/v2/memory/observations", json=payload).json()["usage"]["model_calls"]
            == 0
        )
        assert len(calls) == 1
        assert (
            client.post(
                "/api/v2/memory/observations", json={**payload, "text": "changed"}
            ).status_code
            == 409
        )
        assert client.post(f"/api/v2/memory/{entry['memory_id']}/confirm").json()["layer"] == "deep"
        assert (
            client.patch(
                f"/api/v2/memory/{entry['memory_id']}", json={"value": "arbitrary"}
            ).status_code
            == 422
        )
        assert client.get("/api/v2/memory").json()["fields"]
        assert client.delete(f"/api/v2/memory/{entry['memory_id']}").status_code == 200
        assert client.post("/api/v2/memory/observations", json=payload).json()["entries"] == []
        # Relabelled old text cannot resurrect a forgotten canonical value.
        assert (
            client.post(
                "/api/v2/memory/observations", json={**payload, "source_id": "relabelled"}
            ).json()["entries"]
            == []
        )
        assert client.delete("/api/v2/memory/sources/event_one").status_code == 200
        assert client.get("/api/v2/memory").json()["entries"] == []


def test_invalid_interpretation_stops_generation_and_cannot_save_memory(tmp_path):
    app, _ = setup(tmp_path, invalid_evidence=True)
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/generations",
            json={
                "source": {
                    "type": "inputs",
                    "instruction": "请详细讲解",
                    "inputs": [{"type": "text", "text": "x=1"}],
                },
                "memory": {"session_id": "s1"},
            },
        )
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "needs_review"
        assert job["error"]["code"] == "invalid_request_interpretation"
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get("/api/v2/memory").json()["learning"]["tasks"] == []
