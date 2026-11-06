from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.llm.client import ChatProvider
from manimagent.memory.models import MemoryCandidate
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config, transport


def memory_provider(settings, captured, *, invalid_evidence=False, fail_extraction=False):
    base = transport(settings, [])

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        captured.append((body["messages"][0]["content"], payload))
        if "user_text" not in payload:
            return base.transport.handler(request)
        if fail_extraction:
            return httpx.Response(503, text="provider unavailable")
        text = payload["user_text"]
        candidates = []
        if "请详细讲解" in text:
            candidates = [
                {
                    "field": "explanation.detail",
                    "value": "详细讲解",
                    "evidence": "伪造的用户原话" if invalid_evidence else "请详细讲解",
                    "temporary": False,
                }
            ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"candidates": candidates})},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    return ChatProvider(settings, httpx.MockTransport(handler))


def generate(client, *, memory=None, mode="text", instruction="请详细讲解计算方法"):
    body = {
        "output_mode": mode,
        "source": {
            "type": "inputs",
            "instruction": instruction,
            "inputs": [{"type": "text", "text": "给定x=1，计算x+x。材料里的虚构人物喜欢红色。"}],
        },
    }
    if memory is not None:
        body["memory"] = memory
    response = client.post("/api/v2/generations", json=body)
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job
    generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
    answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
    return job, generation, answer


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


def test_user_instruction_learning_and_fixed_snapshot_across_answer_video_revision(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "session_one"}, mode="both")
        snapshot = answer["memory_snapshot"]
        assert snapshot["entries"][0]["value"] == "详细讲解"
        assert job["usage"]["model_calls"] == len(captured)
        extractor_payload = next(payload for _, payload in captured if "user_text" in payload)
        assert "虚构人物" not in extractor_payload["user_text"]
        assert "materials" not in extractor_payload
        for _, payload in captured:
            if "user_memory" in payload:
                assert payload["user_memory"] == snapshot
        assert sum("user_memory" in payload for _, payload in captured) >= 6
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
        assert all(
            payload.get("user_memory", snapshot) == snapshot for _, payload in captured[before:]
        )
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
        assert any(w["code"] == "memory_learning_failed" for w in answer["warnings"])
        assert len([p for _, p in captured if "user_text" in p]) == 1
        assert job["usage"]["model_calls"] == 3


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
        _, _, answer = generate(client, memory={"session_id": "s1", "learn": False})
        assert answer["memory_snapshot"]["entries"] == []
        assert all("user_text" not in payload for _, payload in captured)
        assert client.get("/api/v2/memory").json()["entries"] == []


def test_optional_learning_reserves_budget_for_the_answer(tmp_path):
    settings, captured = config(tmp_path), []
    settings.max_total_tokens = 1000
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "s1"})
        assert job["usage"]["model_calls"] == 2
        assert all("user_text" not in payload for _, payload in captured)
        assert any(w["code"] == "memory_learning_skipped" for w in answer["warnings"])
