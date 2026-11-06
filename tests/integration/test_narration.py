from __future__ import annotations

import wave

import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.errors import ContentError
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config, submit, transport


class NarratedRenderer(TemplateRenderer):
    def mux_audio(self, directory, audio, plan, budget):
        with wave.open(str(audio), "rb") as wav:
            assert abs(wav.getnframes() / wav.getframerate() - plan.duration_seconds) < 0.001
        assert plan.time_source == "audio_measured"
        (directory / "narrated.mp4").write_bytes(b"controlled-narrated-movie")
        return {
            "video_path": "narrated.mp4",
            "media": {"duration_seconds": plan.duration_seconds, "audio_streams": 1},
        }


@pytest.mark.parametrize("fail", [False, True])
def test_tts_publication_or_partial_keeps_answer(tmp_path, monkeypatch, fail):
    from manimagent.video.tts import MiMoTTS

    settings = config(tmp_path)
    settings.tts.enabled = True
    monkeypatch.setenv("MIMO_API_KEY", "test-private-key")

    def synthesize(self, text, path, budget):
        if fail:
            raise ContentError("tts_auth", "语音服务鉴权失败")
        # Longer than original beat: verifies re-timing instead of cutting the speech.
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x10\x00" * (18 * 8000))
        return {"duration_seconds": 18.0, "sample_rate": 8000, "frames": 144000}

    monkeypatch.setattr(MiMoTTS, "synthesize", synthesize)
    with TestClient(create_app(settings, transport(settings, []), NarratedRenderer())) as client:
        job = submit(client)
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        assert generation["answer_id"]
        if fail:
            assert job["status"] == "partial"
            assert job["error"]["code"] == "tts_auth"
            assert generation["video_id"] is None
            return
        assert job["status"] == "completed", job
        vid = generation["video_id"]
        video = client.get(f"/api/v2/videos/{vid}").json()
        plan = client.get(f"/api/v2/videos/{vid}/plan").json()
        assert video["audio_asset_id"] and video["time_source"] == "audio_measured"
        assert video["cue_time_source"] == "estimated"
        assert plan["profile_id"] == "standard_narrated" and plan["timing_revision"] == 2
        assert video["duration_seconds"] == 180
        assert client.get(f"/api/v2/videos/{vid}/content").content == b"controlled-narrated-movie"
        for scene in plan["scenes"]:
            for beat in scene["beats"]:
                assert beat["narration_end_seconds"] <= beat["end_seconds"] - 0.99
                assert beat["time_source"] == "audio_measured"
        raw = client.get(f"/api/v2/files/{video['audio_asset_id']}/content")
        assert raw.status_code == 200 and raw.content[:4] == b"RIFF"


def test_explicit_silent_profile_overrides_enabled_tts(tmp_path, monkeypatch):
    settings = config(tmp_path)
    settings.tts.enabled = True
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    with TestClient(create_app(settings, transport(settings, []), TemplateRenderer())) as client:
        job = submit(client, "text")
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        response = client.post(
            "/api/v2/videos",
            json={"answer_id": generation["answer_id"], "video_profile_id": "standard_silent"},
        )
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        video = client.get(f"/api/v2/videos/{job['result']['id']}").json()
        assert video["audio_asset_id"] is None and video["time_source"] == "estimated"


@pytest.mark.parametrize(
    "default_enabled,profile", [(True, "standard_silent"), (False, "standard_narrated")]
)
def test_generation_profile_overrides_speech_default_through_final_publication(
    tmp_path, monkeypatch, default_enabled, profile
):
    from manimagent.video.tts import MiMoTTS

    settings = config(tmp_path)
    settings.tts.enabled = default_enabled
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    spoken = []

    def synthesize(self, text, path, budget):
        spoken.append(text)
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x00" * (18 * 8000))
        return {"duration_seconds": 18.0, "sample_rate": 8000, "frames": 144000}

    monkeypatch.setattr(MiMoTTS, "synthesize", synthesize)
    with TestClient(create_app(settings, transport(settings, []), NarratedRenderer())) as client:
        if profile == "standard_narrated":
            speech = client.get("/api/v2/tts-settings").json()
            assert (
                client.put(
                    "/api/v2/tts-settings",
                    json={
                        "expected_revision": speech["revision"],
                        "draft": {"config": speech["config"], "api_key": "narration-test-only"},
                    },
                ).status_code
                == 200
            )
        response = client.post(
            "/api/v2/generations",
            json={
                "output_mode": "both",
                "video_profile_id": profile,
                "source": {
                    "type": "inputs",
                    "instruction": "完整讲解计算方法",
                    "inputs": [{"type": "text", "text": "给定 x=1，计算 x+x 并解释。"}],
                },
            },
        )
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        assert generation["video_profile_id"] == profile
        video = client.get(f"/api/v2/videos/{generation['video_id']}").json()
        plan = client.get(f"/api/v2/videos/{generation['video_id']}/plan").json()
        assert plan["profile_id"] == profile
        assert bool(video["audio_asset_id"]) == bool(spoken) == (profile == "standard_narrated")
