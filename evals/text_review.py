"""Opt-in real DeepSeek review. Credentials come from an environment variable or DPAPI file.

Uses isolated API apps, real parsers and real provider calls. Never saves API request headers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi.testclient import TestClient

from evals.cases.text import cases
from evals.support import KEY_ENV, save, wait_job
from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.llm.credentials import dpapi
from tests.support.documents import docx_bytes, pdf_bytes


def run_case(case, output: Path):
    directory = output / case["name"]
    started = time.monotonic()
    result = {
        "name": case["name"],
        "format": case["format"],
        "expected": case.get("expected"),
        "passed": False,
        "real_provider": True,
    }
    config = ModelConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key_env=KEY_ENV,
        max_output_tokens=6000,
        context_window=case.get("context_window", 64000),
    )
    # Separate role bindings exercise routing, without pretending to be different vendors.
    checker = config.model_copy(
        update={"generate_url": "https://api.deepseek.com/v1/chat/completions"}
    )
    settings = Settings(
        runs_dir=directory / "runtime",
        models={"writer": config, "checker": checker},
        roles={"answer_writer": "writer", "answer_checker": "checker"},
        answer_context_tokens=case.get("answer_context_tokens", 48000),
        task_timeout=240,
        model_timeout=90,
        max_model_calls=4,
    )
    try:
        with TestClient(create_app(settings)) as client:
            inputs = []
            fmt = case["format"]
            if fmt == "reject":
                response = client.post(
                    "/api/v2/files", files={"file": (case["filename"], case["content"])}
                )
                result.update(
                    status=response.status_code,
                    passed=response.status_code == case["expected_http"],
                    model_calls=0,
                )
                save(directory / "error.json", response.json())
                return result
            if fmt == "text":
                inputs = [{"type": "text", "text": case["text"]}]
            else:
                files = []
                if fmt == "mixed":
                    files = [("same.txt", b"a=17"), ("same.txt", b"b=25. Find a+b.")]
                elif fmt == "pdf":
                    files = [("material.pdf", pdf_bytes(case["text"], case.get("paint", "")))]
                elif fmt == "docx":
                    files = [("material.docx", docx_bytes(case["text"], case.get("special", "")))]
                else:
                    files = [("material." + fmt, case["text"].encode("utf-8"))]
                for filename, content in files:
                    response = client.post("/api/v2/files", files={"file": (filename, content)})
                    assert response.status_code == 201, f"upload HTTP {response.status_code}"
                    asset = response.json()
                    assert (
                        client.get(f"/api/v2/files/{asset['file_id']}/content").content == content
                    )
                    inputs.append({"type": "file", "file_id": asset["file_id"]})
            response = client.post(
                "/api/v2/generations",
                json={
                    "output_mode": "text",
                    "source": {
                        "type": "inputs",
                        "inputs": inputs,
                        "instruction": "完整回答材料中的所有问题，给出必要推理和结论，引用实际来源。不要假设用户历史。",
                    },
                },
            )
            assert response.status_code == 202, f"generation HTTP {response.status_code}"
            identifier = response.json()["job_id"]
            job = wait_job(client, identifier, timeout=260)
            result.update(
                status=job["status"],
                usage=job["usage"],
                error=job["error"],
                model_calls=job["usage"]["model_calls"],
            )
            save(directory / "job.json", job)
            generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
            save(directory / "generation.json", generation)
            if generation["answer_id"]:
                aid = generation["answer_id"]
                answer = client.get(f"/api/v2/answers/{aid}").json()
                markdown = client.get(f"/api/v2/answers/{aid}/markdown?download=true").text
                html = client.get(f"/api/v2/answers/{aid}/html").text
                save(directory / "answer.json", answer)
                save(directory / "answer.md", markdown)
                save(directory / "answer.html", html)
                assert (
                    markdown == answer["markdown"] == "".join(s["content"] for s in answer["steps"])
                )
                assert all(re.search(p, markdown, re.S | re.I) for p in case["patterns"]), (
                    "expected result/coverage marker missing"
                )
                assert "<script" not in html.lower(), "active script in HTML"
                assert len(markdown) >= case.get("min_answer_chars", 0), "answer unexpectedly short"
                result["answer_chars"] = len(markdown)
            else:
                assert job["result"] is None
            assert job["status"] == case["expected"], (
                f"expected {case['expected']}, got {job['status']}"
            )
            if "expected_error" in case:
                assert job["error"]["code"] == case["expected_error"]
            if "expected_calls" in case:
                assert result["model_calls"] == case["expected_calls"]
            if case["expected"] == "needs_review":
                assert result["model_calls"] == 0, "unsafe parser input spent model calls"
            result["passed"] = True
    except Exception as exc:
        # Error class and local assertion only. Never serialize provider headers/config credentials.
        result["failure"] = type(exc).__name__ + ": " + str(exc)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save(directory / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--names", default="")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.secret_file:
        os.environ[KEY_ENV] = dpapi(args.secret_file.read_bytes(), decrypt=True).decode("utf-8")
    if not os.getenv(KEY_ENV):
        raise SystemExit("Set MANIMAGENT_REVIEW_DEEPSEEK_KEY or provide a Windows DPAPI file")
    selected = cases()
    if args.names:
        names = set(args.names.split(","))
        selected = [c for c in selected if c["name"] in names]
        assert len(selected) == len(names), "unknown case name"
    if args.limit is not None:
        selected = selected[: args.limit]
    results = []
    args.output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        futures = [pool.submit(run_case, c, args.output) for c in selected]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            summary = {
                "model": "deepseek-flash",
                "endpoint": "https://api.deepseek.com",
                "cases": sorted(results, key=lambda x: x["name"]),
                "passed": sum(r["passed"] for r in results),
                "total": len(results),
            }
            save(args.output / "summary.json", summary)
            print(
                json.dumps(
                    {
                        k: result.get(k)
                        for k in (
                            "name",
                            "status",
                            "passed",
                            "model_calls",
                            "elapsed_seconds",
                            "failure",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return int(not results or not all(result["passed"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
