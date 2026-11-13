"""Run one paid, real freshman lesson; retain shared isolated memory between rounds.

Each invocation starts fresh Python imports so reviewed prompt edits take effect.
No synthetic candidates, fake renderer, silent retries, or production memory writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from evals.cases.memory_visual import CASES
from evals.narrated import checked, isolated_settings, memory_context_roles, wait_learning
from evals.support import save, source_hash
from manimagent.api import create_app
from manimagent.video.models import PROMPT_VERSION
from manimagent.video.renderer.media import probe

REPO = Path(__file__).resolve().parents[1]
PROMPT_FILES = (
    "src/manimagent/video/prompts.py",
    "src/manimagent/video/models.py",
    "src/manimagent/memory/service.py",
    "src/manimagent/answers/prompts.py",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_hash():
    return hashlib.sha256(b"".join((REPO / p).read_bytes() for p in PROMPT_FILES)).hexdigest()


def actions_before(client, case, root, clock, destination):
    results = []
    for action in case.get("actions_before", []):
        item = {"action": action, "status": "failed"}
        kind = action["type"]
        if kind == "advance_days":
            clock[0] += timedelta(days=action["days"])
            save(root / "clock.json", {"now": clock[0].isoformat(), "simulated": True})
            item.update(status="completed", now=clock[0].isoformat())
        elif kind == "cleanup":
            item.update(
                status="completed",
                response=checked(client.post("/api/v2/memory/cleanup"), 200).json(),
            )
        elif kind in {"confirm", "correct", "delete"}:
            source = load(root / f"round-{action['source_round']:02d}" / "memory-after.json")
            previous_result = load(root / f"round-{action['source_round']:02d}" / "result.json")
            previous_actions = load(root / f"round-{action['source_round']:02d}" / "actions.json")
            managed_ids = {
                a["memory_id"]
                for a in previous_actions
                if a.get("status") == "completed" and "memory_id" in a
            }
            current = checked(client.get("/api/v2/memory"), 200).json()["entries"]
            existing = {e["memory_id"] for e in current}
            target = [
                e
                for e in source["entries"]
                if e["memory_id"] in existing
                and (
                    e["memory_id"] in managed_ids
                    or any(v["source_id"] == previous_result["job_id"] for v in e["evidence"])
                )
                and action.get("value_contains", "") in e["value"]
                and action.get("field_contains", "") in e["field"]
            ]
            if len(target) != 1:
                item.update(
                    reason="target_not_unique", candidate_ids=[e["memory_id"] for e in target]
                )
            else:
                identifier = target[0]["memory_id"]
                if kind == "correct":
                    response = client.patch(
                        f"/api/v2/memory/{identifier}",
                        json={
                            "value": action["value"],
                            "confirm_long_term": action.get("confirm_long_term", False),
                        },
                    )
                elif kind == "confirm":
                    response = client.post(f"/api/v2/memory/{identifier}/confirm")
                else:
                    response = client.delete(f"/api/v2/memory/{identifier}")
                item.update(
                    status="completed" if response.status_code == 200 else "failed",
                    original_memory_id=identifier,
                    memory_id=response.json().get("memory_id", identifier),
                    http_status=response.status_code,
                    response=response.json(),
                )
        else:
            raise ValueError(f"Unknown evaluation action: {kind}")
        results.append(item)
        save(destination / "actions.json", results)
    save(destination / "actions.json", results)
    return results


def collect_video(client, identifier, destination):
    video = checked(client.get(f"/api/v2/videos/{identifier}"), 200).json()
    plan = checked(client.get(f"/api/v2/videos/{identifier}/plan"), 200).json()
    save(destination / "video.json", video)
    save(destination / "plan.json", plan)
    content = checked(client.get(f"/api/v2/videos/{identifier}/content"), 200).content
    movie = destination / "example.mp4"
    movie.write_bytes(content)
    for index in range(len(video["frame_mapping"])):
        frame = checked(client.get(f"/api/v2/videos/{identifier}/frames/{index}"), 200)
        (destination / f"frame_{index:03d}.png").write_bytes(frame.content)
    media = probe(movie, decode=True, expect_audio=False)
    return {
        "video_id": identifier,
        "media": media,
        "frames": len(video["frame_mapping"]),
        "video_sha256": hashlib.sha256(content).hexdigest(),
        "duration_matches_plan": abs(media["duration_seconds"] - plan["duration_seconds"]) < 0.15,
    }


def run(args):
    root = args.output.resolve()
    destination = root / f"round-{args.round:02d}"
    if destination.exists():
        raise FileExistsError("Round already exists; preserve all attempts")
    if args.round > 1 and not (root / f"round-{args.round - 1:02d}" / "result.json").exists():
        raise ValueError("Run the student trajectory in order")
    destination.mkdir(parents=True)
    case = CASES[args.round - 1]
    assert case["round"] == args.round
    save(destination / "case.json", case)
    for filename in PROMPT_FILES:
        save(destination / "prompts" / filename, (REPO / filename).read_text(encoding="utf-8"))
    clock_file = root / "clock.json"
    clock = [
        datetime.fromisoformat(load(clock_file)["now"])
        if clock_file.exists()
        else datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    ]
    save(clock_file, {"now": clock[0].isoformat(), "simulated": True})
    started = time.monotonic()
    result = {
        "round": args.round,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash_before": prompt_hash(),
        "source_hash_before": source_hash(REPO),
        "mode": "real_llm_and_full_native_silent_video",
        "passed": False,
    }
    try:
        with isolated_settings(None, root) as settings:
            settings.tts.enabled = False
            settings.max_model_calls = 60
            settings.max_total_tokens = 200000
            settings.task_timeout = 900
            settings.video_timeout = 600
            result["evaluation_budgets"] = {
                "max_model_calls": 60,
                "max_total_tokens": 200000,
                "task_timeout": 900,
                "video_timeout": 600,
            }
            for profile in settings.models.values():
                profile.thinking = "disabled"
            for role in ("video_planner", "scene_generator", "plan_checker"):
                settings.roles[role] = settings.model_id_for(role)
            reasoning = settings.model_for("answer_writer").model_copy(deep=True)
            reasoning.thinking = "enabled"
            reasoning.max_output_tokens = 16000
            settings.models["__eval_math_reasoning"] = reasoning
            for role in ("answer_writer", "answer_checker"):
                settings.roles[role] = "__eval_math_reasoning"
            settings.video_max_output_tokens = 16000
            result["model_roles"] = {
                role: {
                    "thinking": settings.model_for(role).thinking,
                    "max_output_tokens": settings.model_for(role).max_output_tokens,
                }
                for role in (
                    "answer_writer",
                    "answer_checker",
                    "video_planner",
                    "scene_generator",
                    "plan_checker",
                )
            }
            result["model"] = settings.model_for("answer_writer").model
            app = create_app(settings)
            manager = app.state.job_manager
            manager.memory.clock = lambda: clock[0]
            with TestClient(app) as client:
                save(
                    destination / "memory-before-actions.json",
                    checked(client.get("/api/v2/memory"), 200).json(),
                )
                result["actions"] = actions_before(client, case, root, clock, destination)
                save(
                    destination / "memory-before.json",
                    checked(client.get("/api/v2/memory"), 200).json(),
                )
                memory = (
                    None
                    if case.get("memory_enabled", True) is False
                    else {
                        "session_id": case["session_id"],
                        "scope": case["scope"],
                        "learn": case.get("learn", True),
                    }
                )
                request = {
                    "output_mode": "both",
                    "video_profile_id": "standard_silent",
                    "source": {
                        "type": "inputs",
                        "instruction": case["instruction"],
                        "inputs": [{"type": "text", "text": case["question"]}],
                    },
                    "memory": memory,
                }
                save(destination / "request.json", request)
                job_id = checked(client.post("/api/v2/generations", json=request), 202).json()[
                    "job_id"
                ]
                result["job_id"] = job_id
                save(destination / "started.json", result)
                last = None
                while True:
                    job = checked(client.get(f"/api/v2/jobs/{job_id}"), 200).json()
                    save(destination / "job.json", job)
                    if job["message"] != last:
                        print(
                            json.dumps(
                                {
                                    "round": args.round,
                                    "elapsed": round(time.monotonic() - started, 1),
                                    "status": job["status"],
                                    "message": job["message"],
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        last = job["message"]
                    if job["status"] not in {"queued", "running"}:
                        break
                    if time.monotonic() - started > settings.task_timeout + 60:
                        raise TimeoutError("Round exceeded configured task deadline")
                    time.sleep(0.5)
                result.update(status=job["status"], error=job["error"], usage=job["usage"])
                if memory and memory["learn"]:
                    learning = wait_learning(client, job_id, destination)
                    save(destination / "memory-learning.json", learning)
                    result["memory_learning"] = {
                        key: learning.get(key)
                        for key in ("state", "usage", "error", "wait_timed_out")
                    }
                else:
                    save(destination / "memory-learning.json", {"state": "disabled"})
                save(
                    destination / "memory-after.json",
                    checked(client.get("/api/v2/memory"), 200).json(),
                )
                record = manager.store.load_record(job_id)
                save(
                    destination / "record.json",
                    {k: v for k, v in record.items() if k != "settings"},
                )
                snapshot = record.get("memory_snapshot")
                save(destination / "memory-snapshot.json", snapshot)
                if snapshot:
                    result["memory_context_roles"] = sorted(
                        set(memory_context_roles(record, snapshot))
                    )
                ref = job.get("resource_ref") or {}
                if ref.get("type") == "generation":
                    generation = checked(client.get(f"/api/v2/generations/{ref['id']}"), 200).json()
                    save(destination / "generation.json", generation)
                    if generation["answer_id"]:
                        answer = checked(
                            client.get(f"/api/v2/answers/{generation['answer_id']}"), 200
                        ).json()
                        save(destination / "answer.json", answer)
                        save(destination / "answer.md", answer["markdown"])
                    if generation["video_id"]:
                        result.update(collect_video(client, generation["video_id"], destination))
                from evals.visual_review import inspect_render_tree

                result["visual"] = inspect_render_tree(
                    manager.store.root / "renders" / job_id, output=destination / "visual"
                )
                result["passed"] = job["status"] == "completed" and result.get(
                    "duration_matches_plan", False
                )
    except Exception as exc:
        # No external exception messages: provider exceptions can contain credentials.
        result["harness_failure_type"] = type(exc).__name__
        print(
            json.dumps({"round": args.round, "harness_failure_type": type(exc).__name__}),
            flush=True,
        )
        raise
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["prompt_hash_after"] = prompt_hash()
        result["fixed_prompt"] = result["prompt_hash_after"] == result["prompt_hash_before"]
        save(destination / "result.json", result)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in {"visual", "actions"}},
                ensure_ascii=False,
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round", type=int, required=True, choices=range(1, 21))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
