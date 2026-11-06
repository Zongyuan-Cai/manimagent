import asyncio
import base64
import io
import json
import time
import wave

import httpx
import pytest
from pydantic import ValidationError

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings, TTSConfig, load_settings
from manimagent.errors import ContentError
from manimagent.schemas import Usage
from manimagent.video.tts import MiMoTTS


def wav_bytes(frames=2400, rate=24000):
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * frames)
    return target.getvalue()


def response_body(raw=None, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "audio": {
                        "data": base64.b64encode(wav_bytes() if raw is None else raw).decode()
                    }
                },
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42},
    }


def provider(handler, **kwargs):
    return MiMoTTS(
        TTSConfig(**kwargs),
        credential_resolver=lambda _: "private-test-key",
        transport=httpx.MockTransport(handler),
    )


def budget():
    return Budget(Settings(), Usage())


def test_contract_preserves_text_and_measures_pcm_without_llm_calls(tmp_path):
    text = "把向量投影到横轴，观察垂直的残差。"

    def handler(request):
        assert str(request.url) == "https://api.xiaomimimo.com/v1/chat/completions"
        assert request.headers["api-key"] == "private-test-key"
        body = json.loads(request.content)
        assert body["model"] == "mimo-v2.5-tts"
        assert body["messages"][1] == {"role": "assistant", "content": text}
        assert body["audio"] == {"format": "wav", "voice": "mimo_default"}
        assert body["stream"] is False
        return httpx.Response(200, json=response_body())

    speech = provider(handler)
    run_budget = budget()
    result = speech.synthesize(text, tmp_path / "beat.wav", run_budget)
    assert result["duration_seconds"] == 0.1
    assert result["frames"] == 2400
    assert result["sample_rate"] == 24000
    assert result["usage"]["total_tokens"] == 42
    assert run_budget.usage.model_calls == 0
    assert speech.calls == 1
    assert (tmp_path / "beat.wav").read_bytes() == wav_bytes()
    assert "private-test-key" not in json.dumps(result)


@pytest.mark.parametrize("finish", ["length", "content_filter", None])
def test_incomplete_audio_never_published(tmp_path, finish):
    speech = provider(lambda _: httpx.Response(200, json=response_body(finish=finish)))
    with pytest.raises(ContentError, match="完整") as exc:
        speech.synthesize("测试旁白", tmp_path / "bad.wav", budget())
    assert exc.value.error.code == "tts_invalid_audio"
    assert not (tmp_path / "bad.wav").exists()


@pytest.mark.parametrize("raw", [b"not a wav", wav_bytes()[:-2], wav_bytes(frames=0)])
def test_corrupt_or_truncated_pcm_rejected(tmp_path, raw):
    speech = provider(lambda _: httpx.Response(200, json=response_body(raw)))
    with pytest.raises(ContentError, match="PCM WAV"):
        speech.synthesize("测试旁白", tmp_path / "bad.wav", budget())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "body", [[], {}, {"choices": []}, {"choices": [{"finish_reason": "stop", "message": None}]}]
)
def test_invalid_response_shape_is_safe(tmp_path, body):
    speech = provider(lambda _: httpx.Response(200, json=body))
    with pytest.raises(ContentError) as exc:
        speech.synthesize("测试旁白", tmp_path / "bad.wav", budget())
    assert exc.value.error.code == "tts_invalid_audio"


def test_base64_validation_and_usage_allowlist(tmp_path):
    body = response_body()
    body["choices"][0]["message"]["audio"]["data"] = "%%%"
    speech = provider(lambda _: httpx.Response(200, json=body))
    with pytest.raises(ContentError):
        speech.synthesize("测试旁白", tmp_path / "bad.wav", budget())
    body = response_body()
    body["usage"] = {"prompt_tokens": "private-test-key", "completion_tokens": True, "secret": 3}
    speech = provider(lambda _: httpx.Response(200, json=body))
    result = speech.synthesize("测试旁白", tmp_path / "ok.wav", budget())
    assert result["usage"] == {}


@pytest.mark.parametrize("status", [302, 401, 403, 402, 404])
def test_no_redirect_or_auth_retry_and_no_echoed_secrets(tmp_path, status):
    speech = provider(
        lambda _: httpx.Response(status, json={"error": "private-test-key provider debug"})
    )
    with pytest.raises(ContentError) as exc:
        speech.synthesize("测试旁白", tmp_path / "bad.wav", budget())
    assert "private-test-key" not in str(exc.value)
    assert "provider debug" not in str(exc.value)
    assert speech.calls == 1


def test_retryable_status_has_separate_bounded_call_budget(tmp_path):
    statuses = iter([503, 200])
    speech = provider(lambda _: httpx.Response(next(statuses), json=response_body()))
    assert speech.synthesize("测试旁白", tmp_path / "ok.wav", budget())["attempts"] == 2
    assert speech.calls == 2
    limited = provider(lambda _: httpx.Response(429), max_calls=1)
    with pytest.raises(BudgetExceeded, match="语音合成调用次数"):
        limited.synthesize("测试旁白", tmp_path / "limited.wav", budget())
    assert limited.calls == 1


def test_response_size_and_audio_duration_are_bounded(tmp_path):
    speech = provider(lambda _: httpx.Response(200, json=response_body()), max_response_bytes=1024)
    with pytest.raises(ContentError, match="大小限制"):
        speech.synthesize("测试旁白", tmp_path / "big.wav", budget())
    speech = provider(lambda _: httpx.Response(200, json=response_body()), max_audio_seconds=0.05)
    with pytest.raises(ContentError, match="时长限制"):
        speech.synthesize("测试旁白", tmp_path / "long.wav", budget())


def test_elapsed_deadline_prevents_any_request(tmp_path):
    speech = provider(lambda _: pytest.fail("expired budgets must not send requests"))
    run_budget = budget()
    run_budget.deadline = time.monotonic() - 1
    with pytest.raises(BudgetExceeded):
        speech.synthesize("测试旁白", tmp_path / "late.wav", run_budget)
    assert speech.calls == 0


def test_wall_clock_timeout_covers_slow_response(tmp_path):
    async def slow(_):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=response_body())

    speech = provider(slow, timeout=0.01, max_retries=0)
    with pytest.raises(ContentError) as exc:
        speech.synthesize("测试旁白", tmp_path / "slow.wav", budget())
    assert exc.value.error.code == "tts_timeout"
    assert not (tmp_path / "slow.wav").exists()


def test_missing_credential_fails_without_http(tmp_path):
    speech = MiMoTTS(TTSConfig(), credential_resolver=lambda _: None)
    with pytest.raises(ContentError) as exc:
        speech.validate_configuration()
    assert exc.value.error.code == "tts_not_configured"
    with pytest.raises(ContentError):
        speech.synthesize("测试旁白", tmp_path / "missing.wav", budget())


def test_configuration_has_no_secret_and_environment_enable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MANIMAGENT_TTS_ENABLED", "true")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("tts:\n  voice: 茉莉\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.tts.enabled
    assert settings.tts.voice == "茉莉"
    assert "api_key" not in settings.tts.model_dump()
    assert Settings().tts.enabled is False
    with pytest.raises(ValidationError):
        TTSConfig(base_url="https://key:secret@example.org/path")
    with pytest.raises(ValidationError):
        TTSConfig(base_url="http://example.org/path")
