"""Opt-in real configured LLM + MiMo + native renderer API acceptance run.

Run from the configured checkout: python -m evals.narrated --output <fresh-directory>.
Secrets are resolved only into temporary process environment variables, never reports.
This explicitly consumes real model usage and is not part of the pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from evals.cases.video import QUESTION
from evals.support import save, source_hash
from manimagent.api import create_app
from manimagent.config import load_settings
from manimagent.llm.settings import LLMSettingsStore
from manimagent.video.renderer.media import probe


@contextmanager
def isolated_settings(config: Path | None, root: Path):
    """Reuse saved UI profiles without copying or persisting their credentials."""
    initial = load_settings(config)
    source = LLMSettingsStore(initial, initial.private_dir or initial.runs_dir / "private")
    settings = source.effective(initial)
    names = []
    try:
        profiles = [*settings.models.values(), settings.tts]
        for profile in profiles:
            secret = source.credentials.resolve(profile.api_key_env)
            if secret:
                name = "MANIMAGENT_EVAL_" + uuid.uuid4().hex.upper()
                os.environ[name] = secret
                names.append(name)
                profile.api_key_env = name
        settings.runs_dir = root / "runtime"
        settings.private_dir = settings.runs_dir / "private"
        settings.tts.enabled = True
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        (settings.runs_dir / ".gitignore").write_text("*\n", encoding="utf-8")
        yield settings
    finally:
        for name in names:
            os.environ.pop(name, None)


def checked(response, expected: int):
    assert response.status_code == expected, f"API returned HTTP {response.status_code}"
    return response


def collect_video(client, root, answer, video_id):
    video = checked(client.get(f"/api/v2/videos/{video_id}"), 200).json()
    plan = checked(client.get(f"/api/v2/videos/{video_id}/plan"), 200).json()
    save(root / "video.json", video)
    save(root / "plan.json", plan)
    content_url = f"/api/v2/videos/{video_id}/content"
    content = checked(client.get(content_url), 200).content
    (root / "example.mp4").write_bytes(content)
    ranged = checked(client.get(content_url, headers={"Range": "bytes=0-1023"}), 206)
    assert ranged.content == content[:1024], "Video range response differs"
    assert ranged.headers["content-range"] == f"bytes 0-1023/{len(content)}"
    assert video["audio_asset_id"], "Narrated video did not publish its WAV asset"
    audio = checked(client.get(f"/api/v2/files/{video['audio_asset_id']}/content"), 200).content
    (root / "narration.wav").write_bytes(audio)
    with wave.open(str(root / "narration.wav"), "rb") as wav:
        audio_duration = wav.getnframes() / wav.getframerate()
        audio_rate = wav.getframerate()
        assert wav.getnframes() > 0, "WAV contains no samples"
    for index, _frame in enumerate(video["frame_mapping"]):
        frame = checked(client.get(f"/api/v2/videos/{video_id}/frames/{index}"), 200)
        (root / f"frame_{index:03d}.png").write_bytes(frame.content)
    for index, report_id in enumerate(video["report_asset_ids"]):
        report = checked(client.get(f"/api/v2/files/{report_id}/content"), 200)
        save(root / f"report_{index:02d}.json", report.json())
    metadata = probe(root / "example.mp4", decode=True, expect_audio=True)
    assert 120 <= metadata["duration_seconds"] <= 360, "Duration outside teaching profile"
    assert abs(metadata["duration_seconds"] - audio_duration) < 0.15, "WAV duration differs"
    assert abs(metadata["duration_seconds"] - plan["duration_seconds"]) < 0.15
    assert video["answer_id"] == answer["answer_id"]
    assert video["answer_hash"] == hashlib.sha256(answer["markdown"].encode()).hexdigest()
    assert plan["answer_hash"] == video["answer_hash"]
    assert video["time_source"] == plan["time_source"] == "audio_measured"
    assert video["cue_time_source"] == "estimated", "Word-level alignment must not be claimed"
    assert plan["profile_id"] == "standard_narrated"
    assert all(unit["intervals"] for unit in video["unit_mapping"])
    beat_count = sum(len(scene["beats"]) for scene in plan["scenes"])
    assert len(video["frame_mapping"]) == beat_count, "Missing beat evidence"
    for scene in plan["scenes"]:
        for beat in scene["beats"]:
            assert beat["time_source"] == "audio_measured"
            assert beat["stable_seconds"] < beat["narration_end_seconds"] <= beat["end_seconds"]
    return {
        "media": metadata,
        "wav_duration_seconds": audio_duration,
        "wav_sample_rate": audio_rate,
        "scene_count": len(plan["scenes"]),
        "beat_count": beat_count,
        "video_sha256": hashlib.sha256(content).hexdigest(),
        "audio_sha256": hashlib.sha256(audio).hexdigest(),
        "range_verified": True,
        "cue_time_source": "estimated",
    }


def run(client, settings, args, root, result):
    memory_options = None
    memory_note = getattr(args, "memory_note", None)
    if memory_note:
        observation = checked(
            client.post(
                "/api/v2/memory/observations",
                json={
                    "text": memory_note,
                    "session_id": "acceptance_history",
                    "source_id": "acceptance_memory_note",
                    "scope": "global",
                },
            ),
            200,
        ).json()
        save(root / "memory-observation.json", observation)
        assert observation["entries"], "Real model did not extract the explicit memory note"
        for entry in observation["entries"]:
            checked(client.post(f"/api/v2/memory/{entry['memory_id']}/confirm"), 200)
        save(root / "memory-before.json", checked(client.get("/api/v2/memory"), 200).json())
        memory_options = {"session_id": "acceptance_lesson", "scope": "global", "learn": True}
        result["memory_observation_usage"] = observation["usage"]
    inputs = [{"type": "text", "text": args.question}]
    for path in args.file:
        with path.open("rb") as stream:
            uploaded = checked(
                client.post("/api/v2/files", files={"file": (path.name, stream)}), 201
            )
        inputs.append({"type": "file", "file_id": uploaded.json()["file_id"]})
    response = checked(
        client.post(
            "/api/v2/generations",
            json={
                "output_mode": "both",
                "source": {"type": "inputs", "instruction": args.question, "inputs": inputs},
                **({"memory": memory_options} if memory_options else {}),
            },
        ),
        202,
    )
    result["job_id"] = job_id = response.json()["job_id"]
    last, answer, generation = None, None, None
    started = time.monotonic()
    while True:
        job = checked(client.get(f"/api/v2/jobs/{job_id}"), 200).json()
        if job["message"] != last:
            print(
                json.dumps(
                    {
                        "elapsed": round(time.monotonic() - started, 1),
                        "status": job["status"],
                        "stage": job["stage"],
                        "message": job["message"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last = job["message"]
        reference = job.get("resource_ref") or {}
        if reference.get("type") == "generation":
            generation = checked(client.get(f"/api/v2/generations/{reference['id']}"), 200).json()
            save(root / "generation.json", generation)
            if generation["answer_id"] and answer is None:
                answer = checked(
                    client.get(f"/api/v2/answers/{generation['answer_id']}"), 200
                ).json()
                save(root / "answer.json", answer)
                save(root / "answer.md", answer["markdown"])
                result["answer_id"] = answer["answer_id"]
        save(root / "job.json", job)
        if job["status"] not in {"queued", "running"}:
            break
        if time.monotonic() - started > min(900, settings.task_timeout) + 30:
            raise TimeoutError("Full-chain acceptance deadline exceeded")
        time.sleep(0.5)
    result.update(status=job["status"], usage=job["usage"], error=job["error"])
    if memory_options:
        memory_state = checked(client.get("/api/v2/memory"), 200).json()
        save(root / "memory-after.json", memory_state)
        if answer:
            snapshot = answer["memory_snapshot"]
            save(root / "memory-snapshot.json", snapshot)
            assert snapshot and snapshot["entries"], "Answer did not bind memory"
            assert not any(w["code"].startswith("memory_") for w in answer["warnings"])
            record = client.app.state.job_manager.store.load_record(job_id)
            memory_payloads, roles = [], []
            for call in record.get("calls", []):
                if call.get("event") != "request":
                    continue
                payload = json.loads(call["request"]["messages"][1]["content"])
                if "user_memory" in payload:
                    memory_payloads.append(payload["user_memory"])
                    roles.append(call["role"])
            assert memory_payloads and all(item == snapshot for item in memory_payloads)
            assert "answer_writer" in roles
            if job["status"] == "completed":
                assert "video_planner" in roles and "scene_generator" in roles
            result["memory"] = {
                "entry_count": len(snapshot["entries"]),
                "consistent_snapshot": True,
                "roles_using_snapshot": sorted(set(roles)),
                "learning_calls": sum(
                    call.get("role") == "memory_extractor" and call.get("event") == "request"
                    for call in record.get("calls", [])
                ),
            }
    if job["status"] != "completed":
        return
    assert generation and answer and generation["video_id"], (
        "Completed generation missing artifacts"
    )
    result["video_id"] = generation["video_id"]
    result.update(collect_video(client, root, answer, generation["video_id"]))
    result["passed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--file", type=Path, action="append", default=[])
    parser.add_argument(
        "--memory-note",
        help="Opt in to real memory extraction, confirmation and a shared answer/video snapshot",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit("Choose a fresh output directory; retain all earlier attempts")
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    source_root = Path(__file__).resolve().parents[1]
    result = {"passed": False, "source_hash_before": source_hash(source_root)}
    try:
        with isolated_settings(args.config, root) as settings:
            result.update(
                model=settings.model_for("answer_writer").model,
                tts_model=settings.tts.model,
                tts_voice=settings.tts.voice,
                video_max_output_tokens=settings.video_max_output_tokens,
                roles={
                    role: {
                        "model": settings.model_for(role).model,
                        "thinking": settings.model_for(role).thinking,
                    }
                    for role in (
                        "answer_writer",
                        "answer_checker",
                        "video_planner",
                        "scene_generator",
                        "plan_checker",
                    )
                },
                uploaded_files=[path.name for path in args.file],
            )
            with TestClient(create_app(settings)) as client:
                run(client, settings, args, root, result)
    except Exception as exc:
        # External exceptions are not serialized; their messages may contain credentials.
        result["failure_type"] = type(exc).__name__
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["source_hash_after"] = source_hash(source_root)
        result["fixed_source"] = result["source_hash_before"] == result["source_hash_after"]
        if not result["fixed_source"]:
            result["passed"] = False
        save(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
