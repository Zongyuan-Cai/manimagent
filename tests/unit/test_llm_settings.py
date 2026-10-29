from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.llm_settings import LLMSettingsStore
from manimagent.providers import ChatProvider
from manimagent.schemas import TextGenerationRequest
from manimagent.text_jobs import TextJobManager
from tests.support import wait_job


def configured_model(**kwargs):
    return ModelConfig(base_url="https://gateway.example/team/v1", model="answer-model", **kwargs)


def update_request(client, key="test-only-secret", **overrides):
    current = client.get("/api/v2/llm-settings").json()
    return {
        "expected_revision": current["revision"],
        "models": {
            "answer_model": {
                "config": configured_model().model_dump(),
                **({"api_key": key} if key else {}),
            }
        },
        "roles": {"answer_writer": "answer_model", "answer_checker": "answer_model"},
        "answer_check": False,
        "answer_context_tokens": 24000,
        **overrides,
    }


def test_save_configuration_immediately_drives_generation_and_survives_restart(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    received = []

    def handler(request):
        received.append(request)
        assert request.headers["authorization"] == "Bearer test-only-secret"
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        ref = payload["allowed_source_refs"][0]
        text = f"# 回答\n\n结果为 3。[[{ref['material_id']}:{ref['block_id']}]]"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        )

    with TestClient(
        create_app(settings, text_provider=ChatProvider(settings, httpx.MockTransport(handler)))
    ) as client:
        assert not client.get("/api/v2/health").json()["ready"]
        response = client.put("/api/v2/llm-settings", json=update_request(client))
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["revision"] == 1 and view["models"]["answer_model"]["key_configured"]
        assert "test-only-secret" not in response.text
        assert client.get("/api/v2/health").json()["ready"]
        job = wait_job(
            client,
            client.post(
                "/api/v2/generations",
                json={
                    "source": {
                        "type": "inputs",
                        "inputs": [{"type": "text", "text": "a=3"}],
                        "instruction": "回答",
                    }
                },
            ).json()["job_id"],
        )
        assert job["status"] == "completed", job
        assert len(received) == 1
        for path in (tmp_path / "private" / "records").glob("*.json"):
            assert "test-only-secret" not in path.read_text(encoding="utf-8")
        raw_config = (tmp_path / "private" / "configuration" / "llm.json").read_text(
            encoding="utf-8"
        )
        assert "test-only-secret" not in raw_config
        if os.name == "nt":
            assert (
                "test-only-secret"
                not in (tmp_path / "private" / "configuration" / "credentials.json").read_text()
            )
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        assert client.get("/api/v2/health").json()["ready"]
        assert client.get("/api/v2/llm-settings").json()["roles"]["answer_writer"] == "answer_model"


def test_blank_key_preserves_secret_changes_are_versioned_and_disable_is_explicit(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        original = update_request(client)
        assert client.put("/api/v2/llm-settings", json=original).status_code == 200
        reference = client.get("/api/v2/llm-settings").json()["models"]["answer_model"]["config"][
            "api_key_env"
        ]
        blank = update_request(client, key=None)
        blank["models"]["answer_model"]["config"]["model"] = "changed-model"
        assert client.put("/api/v2/llm-settings", json=blank).status_code == 200
        store = LLMSettingsStore(settings, tmp_path / "private")
        assert store.resolve(reference) == "test-only-secret"
        assert client.get("/api/v2/health").json()["ready"]
        assert client.put("/api/v2/llm-settings", json=original).status_code == 409
        changed = update_request(client, key="new-secret")
        client.put("/api/v2/llm-settings", json=changed)
        new_reference = client.get("/api/v2/llm-settings").json()["models"]["answer_model"][
            "config"
        ]["api_key_env"]
        assert new_reference != reference
        assert (
            store.resolve(new_reference) == "new-secret"
            and store.resolve(reference) == "test-only-secret"
        )
        disabled = update_request(client, key=None)
        disabled["models"]["answer_model"]["clear_api_key"] = True
        assert client.put("/api/v2/llm-settings", json=disabled).status_code == 200
        assert not client.get("/api/v2/health").json()["ready"]


@pytest.mark.parametrize("scope", ["text", "structured"])
def test_draft_probe_sends_the_draft_without_saving_or_exposing_credentials(tmp_path, scope):
    settings = Settings(runs_dir=tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://gateway.example/team/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer draft-secret"
        body = json.loads(request.content)
        assert ("response_format" in body) == (scope == "structured")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"ok":true}' if scope == "structured" else "OK"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    with TestClient(
        create_app(settings, text_provider=ChatProvider(settings, httpx.MockTransport(handler)))
    ) as client:
        response = client.post(
            "/api/v2/llm-settings/test",
            json={
                "model_id": "draft",
                "draft": {"config": configured_model().model_dump(), "api_key": "draft-secret"},
                "scope": scope,
            },
        )
        assert response.status_code == 200 and response.json()["ok"], response.text
        assert len(requests) == 1 and "draft-secret" not in response.text
        assert client.get("/api/v2/llm-settings").json()["revision"] == 0
        assert not (tmp_path / "private" / "configuration" / "credentials.json").exists()


def test_probe_errors_and_configuration_validation_do_not_echo_keys(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    with TestClient(
        create_app(
            settings,
            text_provider=ChatProvider(
                settings, httpx.MockTransport(lambda _: httpx.Response(401, text="private-value"))
            ),
        )
    ) as client:
        draft = {
            "model_id": "test",
            "draft": {"config": configured_model().model_dump(), "api_key": "private-value"},
        }
        response = client.post("/api/v2/llm-settings/test", json=draft)
        assert (
            response.status_code == 502
            and response.json()["error"]["code"] == "provider_auth_error"
        )
        assert "private-value" not in response.text
        draft["draft"]["api_key"] = "private-value\ninvalid"
        response = client.post("/api/v2/llm-settings/test", json=draft)
        assert response.status_code == 422 and "private-value" not in response.text
        update = update_request(client)
        update["roles"]["answer_writer"] = "missing"
        assert client.put("/api/v2/llm-settings", json=update).status_code == 422


def test_cannot_reuse_another_models_secret_reference_or_cross_origin_change(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        )

    with TestClient(
        create_app(settings, text_provider=ChatProvider(settings, httpx.MockTransport(handler)))
    ) as client:
        client.put("/api/v2/llm-settings", json=update_request(client))
        ref = client.get("/api/v2/llm-settings").json()["models"]["answer_model"]["config"][
            "api_key_env"
        ]
        cfg = configured_model().model_dump()
        cfg["api_key_env"] = ref
        response = client.post(
            "/api/v2/llm-settings/test", json={"model_id": "different", "draft": {"config": cfg}}
        )
        assert response.status_code == 422 and not requests
        response = client.put(
            "/api/v2/llm-settings",
            json=update_request(client),
            headers={"Origin": "https://unrelated.example"},
        )
        assert response.status_code == 403
        assert (
            client.get("/api/v2/llm-settings", headers={"Host": "unrelated.example"}).status_code
            == 403
        )


def test_accepted_job_snapshot_and_credentials_keep_their_original_configuration(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    manager = TextJobManager(settings)
    # Work with the control plane before starting the worker, so the task stays queued.
    from manimagent.llm_settings import LLMSettingsUpdate

    first = {
        "expected_revision": 0,
        "models": {"text": {"config": configured_model().model_dump(), "api_key": "first-key"}},
        "roles": {"answer_writer": "text", "answer_checker": "text"},
        "answer_check": False,
    }
    manager.settings = manager.llm_settings.update(
        LLMSettingsUpdate.model_validate(first), manager.settings
    )
    job = manager.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "a=3"}],
                "instruction": "q",
            }
        ),
    )
    record = json.loads(
        (manager.store.root / "records" / f"{job.job_id}.json").read_text(encoding="utf-8")
    )
    original_ref = record["settings"]["models"]["text"]["api_key_env"]
    first["expected_revision"] = 1
    first["models"]["text"]["api_key"] = "second-key"
    first["models"]["text"]["config"]["model"] = "second-model"
    manager.settings = manager.llm_settings.update(
        LLMSettingsUpdate.model_validate(first), manager.settings
    )
    assert record["settings"]["models"]["text"]["model"] == "answer-model"
    assert manager.settings.models["text"].model == "second-model"
    assert manager.llm_settings.resolve(original_ref) == "first-key"


def test_environment_key_is_detected_but_never_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_SETTINGS_TEST_KEY", "environment-secret")
    settings = Settings(
        runs_dir=tmp_path, models={"text": configured_model(api_key_env="LLM_SETTINGS_TEST_KEY")}
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v2/llm-settings")
        assert response.json()["models"]["text"]["key_configured"]
        assert "environment-secret" not in response.text
