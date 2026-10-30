"""Explicit, paid real-provider + native-renderer acceptance test; not run by pytest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.llm_settings import dpapi

QUESTION = """给定 a=(3,2)、b=(2,0)，面向具备向量基础、首次学习正交投影的学生，完整解释并求出 a 在 b 方向上的投影。
正文需包含：b≠0 的前提；几何上投影和垂足的意义；设 p=c b，由 a-p 与 b 正交推导系数公式；
逐项算 a·b、b·b、c、p；区分标量系数、标量投影长度和投影向量；最后用残差 a-p 与 b 的点积验证。
所有推导与结论完整写出，引用材料，不假设历史知识画像。"""
KEY_ENV = "MANIMAGENT_ANIMATION_TEST_KEY"


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--answer-id")
    parser.add_argument("--resume-job-id")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    if args.secret_file:
        os.environ[KEY_ENV] = dpapi(args.secret_file.read_bytes(), decrypt=True).decode()
    if not os.getenv(KEY_ENV):
        raise SystemExit("Set MANIMAGENT_ANIMATION_TEST_KEY or provide a DPAPI secret file")
    root = args.output.resolve()
    cfg = ModelConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key_env=KEY_ENV,
        max_output_tokens=6000,
        context_window=128000,
        thinking="disabled",
    )
    settings = Settings(
        runs_dir=root / "runtime",
        models={"text": cfg},
        answer_context_tokens=64000,
        task_timeout=900,
        model_timeout=60,
        video_timeout=600,
        max_model_calls=30,
        max_total_tokens=300000,
    )
    started = time.monotonic()
    result = {"passed": False, "model": "deepseek-flash", "provider": "https://api.deepseek.com"}
    with TestClient(create_app(settings)) as client:
        if args.answer_id:
            response = client.post(
                "/api/v2/videos",
                json={"answer_id": args.answer_id, "resume_job_id": args.resume_job_id},
            )
        else:
            response = client.post(
                "/api/v2/generations",
                json={
                    "output_mode": "both",
                    "source": {
                        "type": "inputs",
                        "instruction": args.question,
                        "inputs": [{"type": "text", "text": args.question}],
                    },
                },
            )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        last = None
        answer_id = args.answer_id
        while True:
            job = client.get(f"/api/v2/jobs/{job_id}").json()
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
            if (job.get("resource_ref") or {}).get("type") == "generation":
                generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
                save(root / "generation.json", generation)
                if generation["answer_id"]:
                    answer_id = generation["answer_id"]
            if answer_id:
                answer = client.get(f"/api/v2/answers/{answer_id}").json()
                save(root / "answer.json", answer)
                save(root / "answer.md", answer["markdown"])
            if job["status"] not in {"queued", "running"}:
                break
            if time.monotonic() - started > 920:
                raise TimeoutError("Overall acceptance deadline exceeded")
            time.sleep(0.5)
        save(root / "job.json", job)
        result.update(
            status=job["status"],
            job_id=job_id,
            answer_id=answer_id,
            usage=job["usage"],
            elapsed_seconds=round(time.monotonic() - started, 3),
            error=job["error"],
        )
        if job["status"] == "completed":
            video_id = job["result"]["id"] if args.answer_id else generation["video_id"]
            video = client.get(f"/api/v2/videos/{video_id}").json()
            plan = client.get(f"/api/v2/videos/{video_id}/plan").json()
            save(root / "video.json", video)
            save(root / "plan.json", plan)
            content = client.get(f"/api/v2/videos/{video_id}/content").content
            (root / "example.mp4").write_bytes(content)
            ranged = client.get(
                f"/api/v2/videos/{video_id}/content", headers={"Range": "bytes=0-1023"}
            )
            assert ranged.status_code == 206 and ranged.content == content[:1024]
            for i, _frame in enumerate(video["frame_mapping"]):
                image = client.get(f"/api/v2/videos/{video_id}/frames/{i}")
                assert image.status_code == 200
                (root / f"frame_{i:02d}.png").write_bytes(image.content)
            from manimagent.animation_driver import probe

            metadata = probe(root / "example.mp4", decode=True)
            assert 120 <= metadata["duration_seconds"] <= 360
            assert video["answer_hash"] == hashlib.sha256(answer["markdown"].encode()).hexdigest()
            assert video["time_source"] == "estimated" and video["audio_asset_id"] is None
            assert all(u["intervals"] for u in video["unit_mapping"])
            result.update(
                passed=True,
                video_id=video_id,
                media=metadata,
                scene_count=len(plan["scenes"]),
                beat_count=sum(len(s["beats"]) for s in plan["scenes"]),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        save(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
