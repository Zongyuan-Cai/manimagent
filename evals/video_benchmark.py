"""Fixed first-attempt benchmark: 10 topics x 3; no hidden external retries.

Run as python -m evals.video_benchmark --output ... --secret-file ...
The existing E2E runner uses the real provider, native renderer, and full MP4 decode.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from evals.cases.video import TOPICS
from evals.support import save, source_hash
from evals.video import KEY_ENV
from manimagent.llm.credentials import dpapi
from manimagent.video.renderer.process import NativeRenderer

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--workers", type=int, choices=[1, 2], default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--topics", default=",".join(TOPICS))
    parser.add_argument("--thinking", choices=["enabled", "disabled", "default"], default="enabled")
    args = parser.parse_args()
    selected = args.topics.split(",")
    if (
        not set(selected) <= TOPICS.keys()
        or len(selected) != len(set(selected))
        or not 1 <= args.repeats <= 3
    ):
        raise SystemExit("Unknown/duplicate topic or invalid repeats")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise SystemExit("Choose a fresh output directory; previous attempts must remain visible")
    if args.secret_file:
        os.environ[KEY_ENV] = dpapi(args.secret_file.read_bytes(), decrypt=True).decode()
    if not os.getenv(KEY_ENV):
        raise SystemExit("Missing live provider credential")
    version = source_hash(ROOT)

    def run(topic, attempt):
        before = source_hash(ROOT)
        if before != version:
            return {
                "name": f"{topic}-{attempt}",
                "topic": topic,
                "attempt": attempt,
                "passed": False,
                "fixed_source": False,
                "error": {
                    "code": "source_changed",
                    "message": "Source changed; no provider call started",
                },
            }
        directory = args.output / f"{topic}-{attempt}"
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        question = (
            TOPICS[topic]
            + " 面向首次学习本题方法的学生，以完整中文正文讲清所有必要依据，不扩展无关知识，引用题目来源。"
        )
        with (directory / "run.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "evals.video",
                    "--output",
                    str(directory),
                    "--question",
                    question,
                    "--thinking",
                    args.thinking,
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                code = process.wait(timeout=950)
            except subprocess.TimeoutExpired:
                NativeRenderer.stop_tree(process)
                code = -1
        path = directory / "result.json"
        result = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"passed": False, "error": {"code": "runner_failure"}}
        )
        after = source_hash(ROOT)
        return {
            **result,
            "passed": code == 0 and result.get("passed") is True,
            "source_hash_before": before,
            "source_hash_after": after,
            "fixed_source": after == version,
            "name": f"{topic}-{attempt}",
            "topic": topic,
            "attempt": attempt,
            "exit_code": code,
            "wall_seconds": round(time.monotonic() - started, 3),
        }

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(run, topic, attempt)
            for attempt in range(1, args.repeats + 1)
            for topic in selected
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            save(
                args.output / "summary.json",
                {
                    "workers": args.workers,
                    "thinking": args.thinking,
                    "source_hash": version,
                    "fixed_source": all(r["fixed_source"] for r in results),
                    "first_attempt_only": True,
                    "passed": sum(r["passed"] for r in results),
                    "total": len(results),
                    "expected": len(futures),
                    "cases": sorted(results, key=lambda r: r["name"]),
                },
            )
            print(
                json.dumps(
                    {
                        k: result.get(k)
                        for k in ["name", "passed", "status", "wall_seconds", "error"]
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    raise SystemExit(0 if all(r["passed"] and r["fixed_source"] for r in results) else 1)


if __name__ == "__main__":
    main()
