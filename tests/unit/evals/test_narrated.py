import json
import os
from contextlib import nullcontext

import pytest

from evals.narrated import isolated_settings, memory_context_roles
from manimagent.config import ModelConfig, Settings, TTSConfig
from manimagent.llm.settings import LLMSettingsStore
from manimagent.memory.projection import user_context
from manimagent.video.settings import TTSDraft, TTSSettingsStore, TTSSettingsUpdate


@pytest.mark.parametrize("abort", [False, True])
def test_isolated_eval_uses_saved_speech_settings_and_removes_temporary_credentials(
    tmp_path, monkeypatch, abort
):
    initial = Settings(
        runs_dir=tmp_path / "source",
        models={"text": ModelConfig(base_url="https://core.example/v1", model="core")},
    )
    private = initial.runs_dir / "private"
    source = LLMSettingsStore(initial, private)
    initial.models["text"].api_key_env = source.credentials.add({"text": "core-test-only"})["text"]
    saved = TTSSettingsStore(private, source.credentials).update(
        TTSSettingsUpdate(
            expected_revision=0,
            draft=TTSDraft(
                config=TTSConfig(
                    base_url="https://speech.example/v1",
                    model="saved-tts-model",
                    voice="saved-voice",
                ),
                api_key="speech-test-only",
            ),
        ),
        initial,
    )
    monkeypatch.setattr("evals.narrated.load_settings", lambda _: initial)
    references = []
    with pytest.raises(RuntimeError, match="controlled failure") if abort else nullcontext():
        with isolated_settings(None, tmp_path / "evaluation") as settings:
            assert settings.tts.base_url == "https://speech.example/v1"
            assert settings.tts.model == "saved-tts-model" and settings.tts.voice == "saved-voice"
            assert settings.tts.enabled
            references = [settings.models["text"].api_key_env, settings.tts.api_key_env]
            assert len(set(references)) == 2
            assert all(reference.startswith("MANIMAGENT_EVAL_") for reference in references)
            assert os.environ[references[0]] == "core-test-only"
            assert os.environ[references[1]] == "speech-test-only"
            assert "speech-test-only" not in settings.model_dump_json()
            assert not (settings.private_dir / "configuration" / "credentials.json").exists()
            if abort:
                raise RuntimeError("controlled failure")
    assert references and all(reference not in os.environ for reference in references)
    assert source.credentials.resolve(saved.tts.api_key_env) == "speech-test-only"
    assert initial.tts.voice == "mimo_default"


def trace(role, content):
    return {
        "role": role,
        "event": "request",
        "request": {
            "messages": [
                {"role": "system", "content": "test"},
                {"role": "user", "content": content},
            ]
        },
    }


def test_memory_audit_skips_multimodal_reader_and_checks_every_personalized_role():
    snapshot = {
        "captured_at": "audit-only",
        "entries": [
            {"field": "visual.palette", "value": "蓝色", "scope": "global", "layer": "deep"},
            {
                "field": "explanation.pace",
                "value": "这次慢一点",
                "scope": "global",
                "layer": "short_term",
                "temporary": True,
            },
        ],
    }
    calls = [
        trace(
            "material_reader",
            [
                {"type": "text", "text": "read this image"},
                {"type": "image_url", "image_url": {"redacted": True}},
            ],
        ),
        {"role": "material_reader", "event": "http_response", "http_status": 200},
        trace("memory_extractor", json.dumps({"known_fields": [], "user_text": "这次慢一点"})),
    ]
    expected = [
        "answer_writer",
        "answer_checker",
        "video_planner",
        "scene_generator",
        "plan_checker",
    ]
    calls.extend(
        trace(
            role, json.dumps(user_context({}, role=role, snapshot=snapshot, instruction="本次要求"))
        )
        for role in expected
    )
    assert memory_context_roles({"calls": calls}, snapshot) == expected
    # Ignoring unrelated image traces must not hide a mismatched memory projection.
    broken = trace("answer_writer", json.dumps({"user_memory": snapshot, "recent_user_memory": []}))
    with pytest.raises(AssertionError):
        memory_context_roles({"calls": [*calls, broken]}, snapshot)
