from __future__ import annotations

import json

from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.memory.models import MemoryCandidate
from manimagent.memory.projection import model_memory
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import generate, memory_provider, wait_learning


def assert_projected_memory(payload, snapshot, role="scene_generator"):
    projected = model_memory(snapshot, role)
    assert payload["user_memory"] == {"deep": projected["deep"]}
    assert payload["recent_user_memory"] == projected["recent"]
    encoded = json.dumps(payload)
    assert '"captured_at"' not in encoded
    assert '"memory_id"' not in encoded
    assert '"session_id"' not in encoded


def test_memory_disabled_has_no_extraction_or_snapshot(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        _, _, answer = generate(client)
        assert answer["memory_snapshot"] is None
        assert all(
            "user_text" not in payload and "user_memory" not in payload for _, payload in captured
        )
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get("/api/v2/memory").json()["learning"]["tasks"] == []


def test_user_instruction_learning_and_fixed_snapshot_across_answer_video_revision(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "session_one"}, mode="both")
        snapshot = answer["memory_snapshot"]
        assert snapshot["entries"] == []
        learning = wait_learning(client, job["job_id"])
        assert learning["state"] == "completed" and learning["usage"]["model_calls"] == 1
        assert job["usage"]["model_calls"] == sum("user_text" not in p for _, p in captured)
        extractor_payload = next(payload for _, payload in captured if "user_text" in payload)
        assert "虚构人物" not in extractor_payload["user_text"]
        assert "materials" not in extractor_payload
        for _, payload in captured:
            if "user_memory" in payload:
                assert_projected_memory(payload, snapshot)
        assert sum("user_memory" in payload for _, payload in captured) >= 6
        scenes = [
            json.dumps(payload, ensure_ascii=False) for _, payload in captured if "scene" in payload
        ]
        assert len(scenes) >= 3
        assert len({payload.split('"scene":', 1)[0] for payload in scenes}) == 1
        record = client.app.state.job_manager.store.load_record(job["job_id"])
        assert record["memory_snapshot"] == snapshot
        assert "captured_at" in record["memory_snapshot"]
        entry = client.get("/api/v2/memory").json()["entries"][0]
        response = client.patch(f"/api/v2/memory/{entry['memory_id']}", json={"value": "简洁讲解"})
        assert response.status_code == 200, response.text
        revision = client.post(
            "/api/v2/answers/revisions",
            json={"base_answer_id": answer["answer_id"], "markdown": answer["markdown"]},
        )
        assert revision.status_code == 201, revision.text
        assert revision.json()["memory_snapshot"] == snapshot
        before = len(captured)
        response = client.post(
            "/api/v2/videos",
            json={"answer_id": revision.json()["answer_id"], "video_profile_id": "standard_silent"},
        )
        assert response.status_code == 202, response.text
        video_job = wait_job(client, response.json()["job_id"])
        assert video_job["status"] == "completed", video_job
        assert client.get(f"/api/v2/memory/learning/{video_job['job_id']}").status_code == 404
        for _, payload in captured[before:]:
            if "user_memory" in payload:
                assert_projected_memory(payload, snapshot)
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


def test_learning_failure_is_visible_and_keeps_existing_memory_and_answer(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(
        settings, memory_provider(settings, captured, fail_extraction=True), TemplateRenderer()
    )
    entry = app.state.job_manager.memory.observe(
        [MemoryCandidate(field="explanation.detail", value="详细", evidence="详细")],
        session_id="old",
        source_id="original",
        source_text="详细",
    )[0]
    app.state.job_manager.memory.confirm(entry.memory_id)
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "new"})
        assert answer["memory_snapshot"]["entries"][0]["value"] == "详细"
        learning = wait_learning(client, job["job_id"])
        assert learning["state"] == "failed" and learning["error"]
        assert learning["usage"]["model_calls"] == 1
        assert not any(w["code"].startswith("memory_learning_") for w in answer["warnings"])
        assert len([p for _, p in captured if "user_text" in p]) == 1
        assert job["usage"]["model_calls"] == 2
        assert client.get(f"/api/v2/answers/{answer['answer_id']}").json() == answer


def test_management_api_is_idempotent_grounded_and_deletion_does_not_replay(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    payload = {"text": "请详细讲解", "session_id": "one", "source_id": "event_one"}
    with TestClient(app) as client:
        response = client.post("/api/v2/memory/observations", json=payload)
        assert response.status_code == 200, response.text
        entry = response.json()["entries"][0]
        replay = client.post("/api/v2/memory/observations", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["usage"]["model_calls"] == 0
        assert len(captured) == 1
        conflict = client.post("/api/v2/memory/observations", json={**payload, "text": "不同原文"})
        assert conflict.status_code == 409
        confirmed = client.post(f"/api/v2/memory/{entry['memory_id']}/confirm")
        assert confirmed.status_code == 200 and confirmed.json()["layer"] == "deep"
        assert (
            client.patch(
                f"/api/v2/memory/{entry['memory_id']}", json={"value": "\u0001"}
            ).status_code
            == 422
        )
        assert client.get("/api/v2/memory").json()["entries"][0]["value"] == entry["value"]
        assert client.delete(f"/api/v2/memory/{entry['memory_id']}").status_code == 200
        assert client.post("/api/v2/memory/observations", json=payload).json()["entries"] == []
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.post("/api/v2/memory/cleanup").status_code == 200
        assert client.post("/api/v2/memory/missing/confirm").status_code == 404
        assert client.patch("/api/v2/memory/missing", json={"value": " "}).status_code == 422


def test_ungrounded_extraction_never_writes_memory(tmp_path):
    settings = config(tmp_path)
    app = create_app(
        settings, memory_provider(settings, [], invalid_evidence=True), TemplateRenderer()
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/memory/observations",
            json={"text": "请详细讲解", "session_id": "s1", "source_id": "e1"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ungrounded_memory"
        assert client.get("/api/v2/memory").json()["entries"] == []


def test_retrieval_only_does_not_add_evidence(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "s1", "learn": False})
        assert answer["memory_snapshot"]["entries"] == []
        assert all("user_text" not in payload for _, payload in captured)
        assert client.get("/api/v2/memory").json()["entries"] == []
        assert client.get(f"/api/v2/memory/learning/{job['job_id']}").status_code == 404


def test_same_material_and_preferences_keep_prefix_across_sessions_but_corrections_apply(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    store = app.state.job_manager.memory
    candidate = MemoryCandidate(field="explanation.detail", value="详细", evidence="详细")
    entry = store.observe([candidate], session_id="seed", source_id="seed", source_text="详细")[0]
    store.confirm(entry.memory_id)
    with TestClient(app) as client:
        _, generation, first = generate(client, memory={"session_id": "one", "learn": False})
        first_system, first_payload = captured[0]
        store.observe([candidate], session_id="evidence", source_id="evidence", source_text="详细")

        def again(session):
            before = len(captured)
            response = client.post(
                "/api/v2/generations",
                json={
                    "source": {
                        "type": "materials",
                        "material_ids": generation["material_ids"],
                        "instruction": "换一种方式说明计算过程",
                    },
                    "memory": {"session_id": session, "learn": False},
                },
            )
            assert response.status_code == 202, response.text
            job = wait_job(client, response.json()["job_id"])
            assert job["status"] == "completed", job
            generated = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
            answer = client.get(f"/api/v2/answers/{generated['answer_id']}").json()
            return captured[before], answer

        (second_system, second_payload), second = again("two")
        assert first["memory_snapshot"] != second["memory_snapshot"]
        assert first_system == second_system
        assert (
            json.dumps(first_payload).split('"instruction":', 1)[0]
            == json.dumps(second_payload).split('"instruction":', 1)[0]
        )
        assert first_payload["allowed_source_refs"] == second_payload["allowed_source_refs"]
        assert "created_at" not in json.dumps(second_payload)
        store.correct(entry.memory_id, "简洁")
        (_, changed), third = again("three")
        assert changed["user_memory"] != second_payload["user_memory"]
        assert third["memory_snapshot"]["entries"][0]["value"] == "简洁"
        assert (
            client.get(f"/api/v2/answers/{first['answer_id']}").json()["memory_snapshot"]
            == first["memory_snapshot"]
        )


def test_optional_learning_has_an_independent_budget_from_the_answer(tmp_path):
    settings, captured = config(tmp_path), []
    settings.max_total_tokens = 1000
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "s1"})
        assert job["usage"]["model_calls"] == 2
        learning = wait_learning(client, job["job_id"])
        assert learning["state"] == "completed" and learning["usage"]["model_calls"] == 1
        assert sum("user_text" in payload for _, payload in captured) == 1
        assert answer["memory_snapshot"]["entries"] == []
        assert client.get(f"/api/v2/jobs/{job['job_id']}").json()["usage"] == job["usage"]
