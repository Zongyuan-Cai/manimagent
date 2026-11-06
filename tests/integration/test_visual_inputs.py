from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ChatProvider
from manimagent.llm.settings import LLMSettingsUpdate
from manimagent.schemas import ParseRequest
from tests.support import wait_job
from tests.support.documents import pdf_bytes


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(stream, format="PNG")
    return stream.getvalue()


def configuration(tmp_path, *, vision=True):
    return Settings(
        runs_dir=tmp_path,
        models={
            "text": ModelConfig(
                base_url="https://models.example/v1", model="text", auth_mode="none"
            ),
            "reader": ModelConfig(
                base_url="https://models.example/v1",
                model="reader",
                auth_mode="none",
                supports_vision=vision,
            ),
        },
        roles={"material_reader": "reader"},
    )


def provider(settings, calls, *, uncertain=False):
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["model"] == "reader":
            content = body["messages"][1]["content"]
            assert content[0]["type"] == "text"
            encoded = content[1]["image_url"]["url"].split(",", 1)[1]
            assert base64.b64decode(encoded).startswith(b"\xff\xd8")
            result = json.dumps(
                {
                    "text": "已知 x=2，计算 x+3。",
                    "uncertain": uncertain,
                    "issues": ["右下角条件模糊"] if uncertain else [],
                }
            )
        else:
            payload = json.loads(body["messages"][1]["content"])
            if "candidate_markdown" in payload:
                result = json.dumps({"decision": "pass", "message": "通过"})
            else:
                ref = payload["allowed_source_refs"][0]
                result = f"# 解答\n\nx+3=5。[[{ref['material_id']}:{ref['block_id']}]]"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": result}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 30},
            },
        )

    return ChatProvider(settings, httpx.MockTransport(respond))


def submit_picture(client, kind="png"):
    source = (
        ("题目.png", picture(), "image/png")
        if kind == "png"
        else (
            "题目.pdf",
            pdf_bytes("The essential triangle must be read visually.", "S"),
            "application/pdf",
        )
    )
    uploaded = client.post("/api/v2/files", files={"file": source})
    assert uploaded.status_code == 201, uploaded.text
    response = client.post(
        "/api/v2/generations",
        json={
            "output_mode": "text",
            "source": {
                "type": "inputs",
                "instruction": "解释图片中的题目",
                "inputs": [{"type": "file", "file_id": uploaded.json()["file_id"]}],
            },
        },
    )
    assert response.status_code == 202, response.text
    return wait_job(client, response.json()["job_id"])


@pytest.mark.parametrize("kind", ["png", "pdf"])
def test_image_to_answer_retains_unverified_source_and_does_not_log_image_payload(tmp_path, kind):
    settings, calls = configuration(tmp_path), []
    with TestClient(create_app(settings, provider(settings, calls))) as client:
        job = submit_picture(client, kind)
        assert job["status"] == "completed", job
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
        assert answer["warnings"][0]["code"] == "visual_reading"
        material = client.get(f"/api/v2/materials/{generation['material_ids'][0]}").json()
        assert material["blocks"][0]["source"]["verification"] == "unverified"
        assert material["blocks"][0]["source"]["locator"] == (
            {"image": 1} if kind == "png" else {"page": 1}
        )
        record = client.app.state.job_manager.store.load_record(job["job_id"])
        serialized = json.dumps(record)
        assert "data:image/" not in serialized
        assert '"redacted": true' in serialized
        assert len(calls) == 3


@pytest.mark.parametrize(
    "vision,uncertain,expected",
    [(False, False, "vision_not_configured"), (True, True, "content_uncertain")],
)
@pytest.mark.parametrize("kind", ["png", "pdf"])
def test_missing_vision_or_uncertain_reading_does_not_invent_an_answer(
    tmp_path, vision, uncertain, expected, kind
):
    settings, calls = configuration(tmp_path, vision=vision), []
    with TestClient(create_app(settings, provider(settings, calls, uncertain=uncertain))) as client:
        job = submit_picture(client, kind)
        assert job["status"] == ("needs_review" if vision else "needs_input"), job
        assert job["error"]["code"] == expected
        assert job["error"]["details"]["filename"] == f"题目.{kind}"
        assert len(calls) == int(vision)
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        assert generation["answer_id"] is None


def test_accepted_visual_task_executes_original_reader_and_key_after_configuration_change(tmp_path):
    settings = configuration(tmp_path)
    calls = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "reader"
        assert request.url.host == "models.example"
        assert request.headers["authorization"] == "Bearer original-reader-test-key"
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"text": "原始题设 x=2", "uncertain": False, "issues": []}
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
        )

    manager = JobManager(settings, ChatProvider(settings, httpx.MockTransport(respond)))
    payload = {
        "expected_revision": 0,
        "models": {
            "text": {"config": settings.models["text"].model_dump()},
            "reader": {
                "config": {**settings.models["reader"].model_dump(), "auth_mode": "bearer"},
                "api_key": "original-reader-test-key",
            },
        },
        "roles": {"answer_writer": "text", "answer_checker": "text", "material_reader": "reader"},
    }
    manager.configure(LLMSettingsUpdate.model_validate(payload))
    asset = manager.store.put_file(io.BytesIO(picture()), "题目.png")
    accepted = manager.submit(
        "parse", ParseRequest(inputs=[{"type": "file", "file_id": asset.file_id}])
    )
    payload["expected_revision"] = 1
    payload["models"]["reader"]["api_key"] = "replacement-reader-test-key"
    payload["models"]["reader"]["config"].update(
        base_url="https://replacement.example/v1", model="replacement", supports_vision=False
    )
    payload["roles"]["material_reader"] = None
    manager.configure(LLMSettingsUpdate.model_validate(payload))
    assert not manager.settings.model_for("material_reader").supports_vision
    # Run exactly the accepted queue item after rotating the live defaults.
    identifier = manager.queue.get_nowait()
    try:
        completed = manager.execute(identifier)
        assert completed.status == "completed", completed.error
        assert len(calls) == 1
        serialized = json.dumps(manager.store.load_record(accepted.job_id))
        assert "original-reader-test-key" not in serialized
        assert "replacement-reader-test-key" not in serialized
        assert "data:image/" not in serialized
    finally:
        manager.queue.task_done()
        manager.slots.release()
        manager.stop()
