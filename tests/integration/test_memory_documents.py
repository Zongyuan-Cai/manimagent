import json

from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.memory.audit import memory_context
from manimagent.schemas import TextGenerationRequest
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config
from tests.support.memory import generate, memory_provider, observe


def test_saved_file_and_api_follow_actual_answer_and_video_contexts(tmp_path):
    settings, captured = config(tmp_path), []
    app = create_app(settings, memory_provider(settings, captured), TemplateRenderer())
    manager = app.state.job_manager
    visual = observe(manager.memory, value="蓝色", field="visual.palette")
    manager.memory.confirm(visual.memory_id)
    observe(manager.memory, source="detail", value="详细", field="explanation.detail")
    with TestClient(app) as client:
        job, _, answer = generate(client, memory={"session_id": "new", "learn": False}, mode="both")
        endpoint = f"/api/v2/jobs/{job['job_id']}/memory-context"
        response = client.get(endpoint)
        assert response.status_code == 200
        context = response.json()
        assert context["memory_snapshot"] == answer["memory_snapshot"]
        record = manager.store.load_record(job["job_id"])
        requests = [event for event in record["calls"] if event.get("event") == "request"]
        assert len(context["calls"]) == len(requests)
        for call, traced in zip(context["calls"], requests, strict=True):
            payload = json.loads(traced["request"]["messages"][1]["content"])
            assert call["projection"] == {
                key: payload[key] for key in ("user_memory", "recent_user_memory") if key in payload
            }
        writer = next(call for call in context["calls"] if call["role"] == "answer_writer")
        scene = next(call for call in context["calls"] if call["role"] == "scene_generator")
        assert not next(
            item for item in writer["entries"] if item["memory_id"] == visual.memory_id
        )["included"]
        assert next(item for item in scene["entries"] if item["memory_id"] == visual.memory_id)[
            "included"
        ]
        downloaded = client.get(endpoint + "?download=true")
        assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "no-store"
        saved = manager.store.root / "memory/snapshots" / f"{job['job_id']}.md"
        assert saved.read_text(encoding="utf-8") == downloaded.text
        assert "虚构人物" not in downloaded.text
        for entry in manager.memory.list_entries():
            manager.memory.delete(entry.memory_id)
        video_response = client.post(
            "/api/v2/videos",
            json={"answer_id": answer["answer_id"], "video_profile_id": "standard_silent"},
        )
        assert video_response.status_code == 202
        video_job = wait_job(client, video_response.json()["job_id"])
        inherited = client.get(f"/api/v2/jobs/{video_job['job_id']}/memory-context").json()
        assert inherited["memory_snapshot"] == answer["memory_snapshot"]
        assert inherited["snapshot_source"] == {"answer_id": answer["answer_id"]}
        assert any(call["memory_present"] for call in inherited["calls"])


def test_acceptance_and_shutdown_audits_do_not_claim_calls_that_never_started(tmp_path):
    settings = config(tmp_path)
    manager = create_app(settings).state.job_manager
    job = manager.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "x=1"}],
                "instruction": "解释",
            },
            memory={"session_id": "s", "learn": False},
        ),
    )
    path = manager.store.root / "memory/snapshots" / f"{job.job_id}.md"
    queued = path.read_text(encoding="utf-8")
    assert "queued" in queued and "尚无" in queued
    manager.stop()
    assert "interrupted" in path.read_text(encoding="utf-8")


def test_execution_does_not_fill_a_missing_historical_snapshot_from_current_memory(tmp_path):
    settings, captured = config(tmp_path), []
    manager = create_app(
        settings, memory_provider(settings, captured), TemplateRenderer()
    ).state.job_manager
    observe(manager.memory, value="详细讲解", field="explanation.detail")
    job = manager.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "给定x=1，计算x+x。"}],
                "instruction": "解释计算",
            },
            memory={"session_id": "s", "learn": False},
        ),
    )
    record = manager.store.load_record(job.job_id)
    assert record.pop("memory_snapshot")["entries"]
    manager.store.save("records", job.job_id, record)
    finished = manager.execute(job.job_id)
    assert finished.status == "completed"
    assert captured and all("user_memory" not in payload for _, payload in captured)
    audit = memory_context(manager.store.load_record(job.job_id), status=finished.status)
    assert audit["snapshot_state"] == "unknown"
    assert audit["memory_snapshot"] is None


def test_video_from_legacy_answer_preserves_unknown_memory_snapshot_state(tmp_path):
    settings = config(tmp_path)
    app = create_app(settings, memory_provider(settings, []), TemplateRenderer())
    manager = app.state.job_manager
    with TestClient(app) as client:
        _, _, answer = generate(client)
        answer_id = answer["answer_id"]
        answer.pop("memory_snapshot", None)
        manager.store.save("answers", answer_id, answer)
        response = client.post(
            "/api/v2/videos",
            json={"answer_id": answer_id, "video_profile_id": "standard_silent"},
        )
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        context = client.get(f"/api/v2/jobs/{job['job_id']}/memory-context").json()
        assert context["memory_snapshot"] is None
        assert context["snapshot_state"] == "unknown"
        assert context["snapshot_source"] == {"answer_id": answer_id}
        assert manager.store.load_record(job["job_id"])["memory_snapshot_known"] is False
        assert all(not call["memory_present"] for call in context["calls"])


def test_shutdown_clears_recovered_export_error_in_the_durable_record(tmp_path, monkeypatch):
    from manimagent.memory import audit

    manager = create_app(config(tmp_path)).state.job_manager
    with monkeypatch.context() as fault:
        fault.setattr(
            audit, "write_text", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
        )
        job = manager.submit(
            "generation",
            TextGenerationRequest(
                source={
                    "type": "inputs",
                    "inputs": [{"type": "text", "text": "x=1"}],
                    "instruction": "解释",
                }
            ),
        )
    assert manager.store.load_record(job.job_id)["memory_context_export_error"]
    manager.stop()
    assert "memory_context_export_error" not in manager.store.load_record(job.job_id)
