from __future__ import annotations

import base64
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings, TTSConfig
from manimagent.errors import ContentError
from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ChatProvider
from manimagent.schemas import TextGenerationRequest
from manimagent.video.settings import TTSDraft, TTSProbeRequest, TTSSettingsUpdate


def settings_for(tmp_path):
    return Settings(
        runs_dir=tmp_path,
        tts=TTSConfig(api_key_env="TEST_SPEECH_KEY"),
        models={
            "text": ModelConfig(base_url="https://core.example/v1", model="core", auth_mode="none")
        },
    )


def draft(config, key=None, **kwargs):
    return {"config": config, **({"api_key": key} if key else {}), **kwargs}


def update(client, key=None, **changes):
    current = client.get("/api/v2/tts-settings").json()
    config = {**current["config"], **changes}
    return {"expected_revision": current["revision"], "draft": draft(config, key)}


def wav_body():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 2400)
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"audio": {"data": base64.b64encode(buffer.getvalue()).decode()}},
            }
        ],
        "usage": {"total_tokens": 12},
    }


def test_save_restart_keep_clear_conflict_and_secret_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_SPEECH_KEY", raising=False)
    cfg = settings_for(tmp_path)
    with TestClient(create_app(cfg)) as client:
        initial = client.get("/api/v2/tts-settings").json()
        assert initial["state"] == "disabled" and not initial["key_configured"]
        saved = client.put(
            "/api/v2/tts-settings", json=update(client, "tts-private-test", enabled=True)
        )
        assert saved.status_code == 200
        assert saved.json()["state"] == "ready" and saved.json()["key_source"] == "local"
        assert "tts-private-test" not in saved.text
        first_reference = saved.json()["config"]["api_key_env"]
        stale = update(client, voice="changed_voice")
        kept = client.put("/api/v2/tts-settings", json=stale)
        assert kept.json()["config"]["api_key_env"] == first_reference
        assert client.put("/api/v2/tts-settings", json=stale).status_code == 409
        assert not client.get("/api/v2/llm-settings").json()["models"]["text"]["key_configured"]
        assert client.get("/api/v2/health").json()["tts"]["ready"]
    private = tmp_path / "private" / "configuration"
    assert "tts-private-test" not in (private / "tts.json").read_text(encoding="utf-8")
    with TestClient(create_app(cfg)) as client:
        current = client.get("/api/v2/tts-settings").json()
        assert current["config"]["voice"] == "changed_voice" and current["revision"] == 2
        clearing = update(client)
        clearing["draft"]["clear_api_key"] = True
        cleared = client.put("/api/v2/tts-settings", json=clearing).json()
        assert cleared["state"] == "needs_configuration" and not cleared["ready"]
        assert cleared["key_source"] == "none"


def test_preview_is_bounded_playable_and_does_not_save(tmp_path):
    cfg = settings_for(tmp_path)
    received = []

    def handler(request):
        received.append(request)
        assert request.headers["api-key"] == "preview-only-test-key"
        assert json.loads(request.content)["messages"][1]["content"] == "试听这句话"
        return httpx.Response(200, json=wav_body())

    app = create_app(cfg, text_provider=ChatProvider(cfg, httpx.MockTransport(handler)))
    with TestClient(app) as client:
        before = client.get("/api/v2/tts-settings").json()
        result = client.post(
            "/api/v2/tts-settings/test",
            json={"draft": draft(before["config"], "preview-only-test-key"), "text": "试听这句话"},
        )
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        body = result.json()
        assert body["calls"] == 1 and body["duration_seconds"] == 0.1
        assert body["media_type"] == "audio/wav"
        with wave.open(io.BytesIO(base64.b64decode(body["audio_base64"]))) as audio:
            assert audio.getnframes() == 2400
        assert "preview-only-test-key" not in result.text
        assert client.get("/api/v2/tts-settings").json() == before
        assert len(received) == 1
        assert not (tmp_path / "private" / "configuration").exists()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_failed_preview_never_echoes_remote_body_or_retries(tmp_path, status):
    cfg = settings_for(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="reflected-private-test-key")

    with TestClient(
        create_app(cfg, text_provider=ChatProvider(cfg, httpx.MockTransport(handler)))
    ) as client:
        result = client.post(
            "/api/v2/tts-settings/test",
            json={"draft": draft(cfg.tts.model_dump(), "reflected-private-test-key")},
        )
        assert result.status_code == 422
        assert "reflected-private-test-key" not in result.text
        assert len(calls) == 1


def test_configuration_guards_and_invalid_key_never_echo(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_SPEECH_KEY", raising=False)
    monkeypatch.setenv("OTHER_CORE_KEY", "core-private-test-key")
    cfg = settings_for(tmp_path)
    with TestClient(create_app(cfg)) as client:
        assert (
            client.get(
                "/api/v2/tts-settings", headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/v2/tts-settings", headers={"Host": "other.example"}).status_code == 403
        )
        data = update(client, "secret-with-\n-control")
        response = client.put("/api/v2/tts-settings", json=data)
        assert response.status_code == 422 and "secret-with-" not in response.text
        payload = update(client, api_key_env="OTHER_CORE_KEY")
        response = client.put("/api/v2/tts-settings", json=payload)
        assert response.status_code == 200 and not response.json()["key_configured"]
        assert response.json()["config"]["api_key_env"] == "TEST_SPEECH_KEY"
        result = client.post("/api/v2/tts-settings/test", json={"draft": payload["draft"]})
        assert result.status_code == 422 and result.json()["error"]["code"] == "tts_not_configured"
        invalid = {"draft": draft(cfg.tts.model_dump(), "test"), "text": "x" * 201}
        assert client.post("/api/v2/tts-settings/test", json=invalid).status_code == 422


def test_accepted_tasks_keep_speech_config_and_rotated_secret(tmp_path):
    cfg = settings_for(tmp_path)
    manager = JobManager(cfg)
    first = TTSSettingsUpdate(
        expected_revision=0, draft=TTSDraft(config=cfg.tts, api_key="old-speech-key")
    )
    manager.configure_speech(first)
    request = TextGenerationRequest.model_validate(
        {
            "output_mode": "both",
            "video_profile_id": "standard_narrated",
            "source": {
                "type": "inputs",
                "inputs": [{"type": "text", "text": "求 1+1"}],
                "instruction": "分步解释",
            },
        }
    )
    accepted = manager.submit("generation", request)
    frozen = manager.store.load_record(accepted.job_id)["settings"]["tts"]
    second = TTSSettingsUpdate(
        expected_revision=1,
        draft=TTSDraft(config=TTSConfig(voice="new_voice"), api_key="new-speech-key"),
    )
    manager.configure_speech(second)
    assert frozen["voice"] == "mimo_default" and manager.settings.tts.voice == "new_voice"
    assert manager.llm_settings.credentials.resolve(frozen["api_key_env"]) == "old-speech-key"
    assert (
        manager.llm_settings.credentials.resolve(manager.settings.tts.api_key_env)
        == "new-speech-key"
    )
    assert "old-speech-key" not in json.dumps(manager.store.load_record(accepted.job_id))
    manager.stop()


def test_only_requests_requiring_speech_are_blocked_before_enqueue(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_SPEECH_KEY", raising=False)
    manager = JobManager(settings_for(tmp_path))
    request = TextGenerationRequest.model_validate(
        {
            "output_mode": "both",
            "video_profile_id": "standard_narrated",
            "source": {
                "type": "inputs",
                "inputs": [{"type": "text", "text": "求 1+1"}],
                "instruction": "分步解释",
            },
        }
    )
    with pytest.raises(ContentError) as exc:
        manager.submit("generation", request)
    assert exc.value.error.code == "tts_not_configured" and manager.queue.empty()
    assert not list((manager.store.root / "records").glob("*.json"))
    manager.settings.tts.enabled = True
    with pytest.raises(ContentError):
        manager.submit("generation", request.model_copy(update={"video_profile_id": "auto"}))
    manager.submit("generation", request.model_copy(update={"video_profile_id": "standard_silent"}))
    manager.submit("generation", request.model_copy(update={"output_mode": "text"}))
    manager.stop()


def test_concurrent_preview_returns_busy_instead_of_second_call(tmp_path):
    manager = JobManager(settings_for(tmp_path))
    probe = TTSProbeRequest(
        draft=TTSDraft(config=manager.settings.tts, api_key="temporary-test-key")
    )
    manager.tts_settings.probe_slots.acquire()
    try:
        with pytest.raises(ContentError) as exc:
            manager.probe_speech(probe)
        assert exc.value.error.code == "probe_busy" and exc.value.http_status == 429
    finally:
        manager.tts_settings.probe_slots.release()


def test_changing_speech_host_requires_new_key_or_explicit_clear(tmp_path):
    cfg = settings_for(tmp_path)
    calls = []
    provider = ChatProvider(cfg, httpx.MockTransport(lambda request: calls.append(request)))
    with TestClient(create_app(cfg, text_provider=provider)) as client:
        assert (
            client.put("/api/v2/tts-settings", json=update(client, "old-host-key")).status_code
            == 200
        )
        changed = update(client, base_url="https://different.example/v1")
        for method, path, payload in [
            ("PUT", "/api/v2/tts-settings", changed),
            ("POST", "/api/v2/tts-settings/test", {"draft": changed["draft"]}),
        ]:
            response = client.request(method, path, json=payload)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "credential_origin_changed"
        assert not calls
        same_origin = update(client, base_url="https://api.xiaomimimo.com:443/other/v1")
        assert client.put("/api/v2/tts-settings", json=same_origin).status_code == 200
        changed = update(client, "new-host-key", base_url="https://different.example/v1")
        assert client.put("/api/v2/tts-settings", json=changed).status_code == 200
        cleared = update(client, base_url="https://another.example/v1")
        cleared["draft"]["clear_api_key"] = True
        assert not client.put("/api/v2/tts-settings", json=cleared).json()["key_configured"]


@pytest.mark.parametrize("port", ["0", "70000", "not-a-port"])
def test_invalid_connection_port_is_rejected_as_configuration_error(tmp_path, port):
    cfg = settings_for(tmp_path)
    with TestClient(create_app(cfg)) as client:
        invalid = update(client, base_url=f"https://speech.example:{port}/v1")
        response = client.put("/api/v2/tts-settings", json=invalid)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


def test_explicit_zero_port_never_compares_equal_to_default_origin():
    from manimagent.llm.credentials import same_endpoint_origin

    assert same_endpoint_origin("https://example.com/v1", "https://example.com:443/v2")
    assert not same_endpoint_origin("https://example.com/v1", "https://example.com:0/v2")
