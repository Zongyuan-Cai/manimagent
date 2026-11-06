"""At most three synthetic real MiMo text -> background-memory rounds; no retries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import httpx
from dotenv import dotenv_values
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from manimagent.api import create_app
from manimagent.budget import Budget
from manimagent.config import ModelConfig, Settings
from manimagent.llm.client import ChatProvider

OUT = Path(__file__).resolve().parent
RUNTIME = OUT / "runtime"
CREDENTIAL_FILE = Path(r"C:\Users\20557\.codex\worktrees\docs-av-alignment\manimagent\.env")
KEY_ENV = "MANIMAGENT_LIVE_MEMORY_KEY_20261106"
INSTRUCTION = "请讲解 x=1 时 x+x 的值。我希望以后讲解时每一步都说明依据。"
TERMINAL = {"completed", "partial", "failed", "needs_input", "needs_review", "interrupted"}
LEARNING_TERMINAL = {"completed", "failed", "skipped", "cancelled"}


def main() -> None:
    if RUNTIME.exists() or (OUT / "summary.json").exists():
        raise RuntimeError("This evidence directory already contains a run; do not repeat paid calls")
    raw = dotenv_values(CREDENTIAL_FILE, interpolate=False)
    secret = raw.get("MIMO_API_KEY")
    raw.clear()
    if not secret:
        raise RuntimeError("MIMO_API_KEY is unavailable")

    def save(name, value):
        path = OUT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        path.write_text(text.replace(secret, "[REDACTED]"), encoding="utf-8")

    settings = Settings(
        runs_dir=RUNTIME,
        models={"text": ModelConfig(base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash",
            api_key_env=KEY_ENV, auth_mode="api_key_header", api_key_header="api-key",
            max_output_tokens=2048, thinking="disabled", token_parameter="max_completion_tokens")},
        roles={"answer_writer": "text", "answer_checker": "text"},
        task_timeout=60, model_timeout=20, max_model_calls=2, max_total_tokens=16000,
        memory_learning_timeout=20, memory_learning_max_tokens=16000, answer_check=True,
    )
    source_hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/manimagent").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    save("source-manifest.json", {"files": source_hashes, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    save("settings.json", settings.model_dump(mode="json"))
    calls, rounds = [], []
    active_round = 0
    lock = threading.Lock()

    class GuardedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            body = json.loads(request.content)
            payload = json.loads(body["messages"][1]["content"])
            role = "memory_extractor" if "user_text" in payload else "answer_checker" if "candidate_markdown" in payload else "answer_writer"
            if str(request.url) != "https://api.xiaomimimo.com/v1/chat/completions" or request.headers.get("api-key") != secret:
                raise RuntimeError("Unexpected destination or authentication contract")
            if body.get("model") != "mimo-v2.6-flash" or body.get("thinking") != {"type": "disabled"} or body.get("max_completion_tokens", 999999) > 2048:
                raise RuntimeError("Model call exceeded the declared settings")
            with lock:
                if len(calls) >= 9 or any(call["round"] == active_round and call["role"] == role for call in calls):
                    raise RuntimeError("No retry or additional real call is authorized for this run")
                call = {"index": len(calls) + 1, "round": active_round, "role": role, "request": body}
                calls.append(call)
                save("calls.json", calls)
            started = time.monotonic()
            try:
                async with httpx.AsyncHTTPTransport(retries=0) as transport:
                    response = await transport.handle_async_request(request)
                    content = await response.aread()
                    call["http_status"] = response.status_code
                    try:
                        call["response"] = json.loads(content)
                    except ValueError:
                        call["response"] = {"non_json_body_omitted": True}
                    # aread() has already decoded Content-Encoding. Strip wire
                    # encoding/length headers before rebuilding a buffered response.
                    headers = [(name, value) for name, value in response.headers.multi_items()
                        if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                    return httpx.Response(response.status_code, headers=headers, content=content, request=request)
            except BaseException as exc:
                call["transport_error_type"] = type(exc).__name__
                raise
            finally:
                call["elapsed_seconds"] = round(time.monotonic() - started, 3)
                with lock:
                    save("calls.json", calls)

    def get(client, url):
        response = client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"Local GET failed with HTTP {response.status_code}")
        return response.json()

    def wait(client, url, states, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = get(client, url)
            if value.get("state", value.get("status")) in states:
                return value
            time.sleep(0.2)
        raise RuntimeError("Local task did not reach a terminal state within the bounded observation time")

    previous_env = os.environ.get(KEY_ENV)
    os.environ[KEY_ENV] = secret
    started = time.monotonic()
    outcome = "stopped"
    failure = None
    try:
        provider = ChatProvider(settings, transport=GuardedTransport())
        app = create_app(settings, provider)
        # This process-only harness disables provider retries without altering production source.
        with patch.object(Budget, "allow_retry", return_value=False), TestClient(app) as client:
            previous_entries = []
            for number in range(1, 4):
                active_round = number
                round_started = time.monotonic()
                prefix = f"round-{number}"
                item = {"round": number, "session_id": f"synthetic_independent_{number}", "instruction": INSTRUCTION}
                rounds.append(item)
                body = {"output_mode": "text", "memory": {"session_id": item["session_id"], "scope": "global", "learn": True},
                    "source": {"type": "inputs", "instruction": INSTRUCTION, "inputs": [{"type": "text", "text": "已知 x=1，求 x+x 的值。"}]}}
                save(prefix + "/request.json", body)
                response = client.post("/api/v2/generations", json=body)
                if response.status_code != 202:
                    item["acceptance"] = response.json()
                    raise RuntimeError(f"Generation acceptance failed with HTTP {response.status_code}")
                job_id = response.json()["job_id"]
                item["job_id"] = job_id
                snapshot = app.state.job_manager.store.load_record(job_id)["memory_snapshot"]
                save(prefix + "/submit-snapshot.json", snapshot)
                item["submit_memory_entries"] = snapshot["entries"]
                item["reuses_previous_entry"] = bool({entry["memory_id"] for entry in snapshot["entries"]} & {entry["memory_id"] for entry in previous_entries})
                job = wait(client, f"/api/v2/jobs/{job_id}", TERMINAL, 70)
                save(prefix + "/job.json", job)
                item["foreground_elapsed_seconds"] = round(time.monotonic() - round_started, 3)
                item["foreground_status"] = job["status"]
                item["foreground_usage"] = job["usage"]
                generation = get(client, f"/api/v2/generations/{job['resource_ref']['id']}")
                save(prefix + "/generation.json", generation)
                if generation.get("answer_id"):
                    answer = get(client, f"/api/v2/answers/{generation['answer_id']}")
                    save(prefix + "/answer.json", answer)
                    if answer["memory_snapshot"] != snapshot:
                        raise RuntimeError("Published answer did not preserve the submit snapshot")
                task = wait(client, f"/api/v2/memory/learning/{job_id}", LEARNING_TERMINAL, 35)
                save(prefix + "/learning-task.json", task)
                item["learning_state"] = task["state"]
                item["learning_usage"] = task["usage"]
                item["elapsed_seconds"] = round(time.monotonic() - round_started, 3)
                memory = get(client, "/api/v2/memory")
                save(prefix + "/memory-after.json", memory)
                previous_entries = memory["entries"]
                item["memory_entries_after"] = previous_entries
                if get(client, f"/api/v2/jobs/{job_id}")["usage"] != job["usage"]:
                    raise RuntimeError("Background extraction changed foreground usage")
                save(prefix + "/summary.json", item)
                save("summary.json", {"outcome": "in_progress", "rounds": rounds, "actual_http_calls": len(calls)})
                print(f"round {number}: foreground={job['status']}, learning={task['state']}, actual_calls={len(calls)}", flush=True)
                if job["status"] != "completed" or task["state"] != "completed":
                    raise RuntimeError("Stopped after the first foreground or background failure")
                if job["usage"]["model_calls"] > 2 or task["attempts"] != 1 or task["usage"].get("model_calls", 0) > 1:
                    raise RuntimeError("Declared call bound was not preserved")
            promoted = [entry for entry in previous_entries if entry["layer"] == "deep" and not entry.get("confirmed_at")]
            outcome = "completed_with_automatic_promotion" if promoted else "completed_without_automatic_promotion"
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc).replace(secret, "[REDACTED]")}
        print("stopped: " + failure["type"], flush=True)
    finally:
        if previous_env is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = previous_env
        changed = [name for name, fingerprint in source_hashes.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != fingerprint]
        save("summary.json", {"outcome": outcome, "failure": failure, "rounds": rounds, "actual_http_calls": len(calls),
            "elapsed_seconds": round(time.monotonic() - started, 3), "source_changed_during_run": changed,
            "environment_restored": os.environ.get(KEY_ENV) == previous_env, "scope": "synthetic arithmetic only; no human data, video, TTS or semantic merging"})
        print(f"finished: {outcome}; real HTTP calls={len(calls)}; credentials environment restored", flush=True)


if __name__ == "__main__":
    main()
