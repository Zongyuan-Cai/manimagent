"""Opt-in CLI end-to-end review against the real DeepSeek service."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from manimagent.config import ModelConfig, Settings
from manimagent.llm_settings import dpapi
from scripts.review_live_text import KEY_ENV, ROOT, pdf_bytes, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--secret-file", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    runtime = output / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment[KEY_ENV] = dpapi(args.secret_file.read_bytes(), decrypt=True).decode()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "1"
    model = ModelConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key_env=KEY_ENV,
        max_output_tokens=6000,
        context_window=64000,
    )
    settings = Settings(
        runs_dir=runtime / "runs",
        models={"text": model},
        task_timeout=180,
        model_timeout=90,
        max_model_calls=4,
        answer_context_tokens=48000,
    )
    config = runtime / "config.json"
    save(config, settings.model_dump(mode="json"))  # Environment-variable reference only.
    first, second = runtime / "a.txt", runtime / "b.pdf"
    first.write_text("a=17", encoding="utf-8")
    second.write_bytes(pdf_bytes("b=25. Find a+b."))
    prefix = [sys.executable, "-m", "manimagent", "--config", str(config)]
    cases = [
        ("doctor", ["doctor"], 0, None),
        ("text", ["answer", "--question", "计算13+29并解释，引用来源。"], 0, "completed"),
        (
            "mixed_files",
            [
                "answer",
                "--question",
                "结合两份文件计算a+b，引用实际来源。",
                "--file",
                str(first),
                "--file",
                str(second),
            ],
            0,
            "completed",
        ),
        (
            "missing_data",
            ["answer", "--question", "请计算数据的数值均值，但我尚未提供任何数据。"],
            1,
            "needs_input",
        ),
        (
            "missing_file",
            ["answer", "--question", "解答文件。", "--file", str(runtime / "absent.txt")],
            2,
            None,
        ),
    ]
    results = []
    for name, arguments, expected_code, expected_status in cases:
        started = time.monotonic()
        before = set((runtime / "runs" / "private" / "jobs").glob("*.json"))
        result = {"name": name, "passed": False, "model_calls": 0}
        try:
            completed = subprocess.run(
                [*prefix, *arguments],
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=200,
                check=False,
            )
            result["exit_code"] = completed.returncode
            assert completed.returncode == expected_code, "CLI exit code differs"
            after = set((runtime / "runs" / "private" / "jobs").glob("*.json"))
            for job_path in sorted(after - before):
                job = json.loads(job_path.read_text(encoding="utf-8"))
                result.update(
                    status=job["status"],
                    usage=job["usage"],
                    model_calls=job["usage"]["model_calls"],
                )
                save(output / name / "job.json", job)
                assert job["status"] == expected_status
            if expected_status == "completed":
                assert "42" in completed.stdout and "[[material_" in completed.stdout
                save(output / name / "answer.md", completed.stdout)
            if expected_status == "needs_input":
                assert json.loads(completed.stderr)["error"]["code"] == "source_incomplete"
            if name == "doctor":
                assert json.loads(completed.stdout)["ready"] is True
            result["passed"] = True
        except Exception as exc:
            result["failure"] = type(exc).__name__ + ": " + str(exc)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        save(output / name / "result.json", result)
        save(
            output / "summary.json",
            {"cases": results, "passed": sum(r["passed"] for r in results), "total": len(results)},
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
