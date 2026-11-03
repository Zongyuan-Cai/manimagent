"""Fixed first-attempt benchmark: 10 topics x 3; no hidden external retries.

Run as python -m scripts.review_live_animation --output ... --secret-file ...
The existing E2E runner uses the real provider, native renderer, and full MP4 decode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from manimagent.animation_renderer import NativeRenderer
from manimagent.llm_settings import dpapi
from scripts.test_animation_e2e import KEY_ENV, QUESTION, save

ROOT = Path(__file__).resolve().parents[1]
TOPICS = {
    "projection": QUESTION,
    "linear_system": "解二元一次方程组 2x+y=7、x-y=2。完整说明消元依据、两种消元方向、逐步计算、回代检验，以及为什么解唯一。",
    "quadratic": "解方程 x^2-5x+6=0。解释因式分解的依据、零乘积法则，逐步求根，用求根公式独立核对，并将两个根分别代回原式验证。",
    "derivative": "用导数定义推导 f(x)=x^2 的导数，再求 x=3 处切线方程。解释增量、差商、h不能为零、极限与斜率的区别，最后核对切点。只用公式和数值解释，不要求函数曲线图。",
    "integral": "计算定积分 ∫_0^2 (3x^2+2x+1) dx。说明原函数、牛顿莱布尼茨公式、上下限代入次序，逐项计算并给出结果，再用原函数求导检验。只用公式讲解。",
    "bayes": "盒A有3红2蓝，盒B有1红4蓝，先等概率选盒，再随机抽球。已知抽到红球，求来自盒A的概率。解释条件概率、全概率、贝叶斯公式，列出联合概率并用自然频率核对。",
    "matrix_inverse": "给定 A=[[2,1],[1,1]]，求行列式与逆矩阵。解释可逆条件、二阶求逆公式、逐项计算，并完整计算 A乘A逆 的四个元素核对单位矩阵。",
    "triangle_area": "在直角坐标系中给定 A=(0,0)、B=(4,0)、C=(1,3)。用底乘高及二维行列式两种方法求面积，解释有向面积与绝对值、底和高的选择，并核对两法结果。",
    "geometric_sum": "推导等比数列前n项和公式，区分q=1与q不等于1；再计算首项3、公比2、前5项和。逐步展示错位相减、分母条件、代入与直接相加验证。",
    "variance": "给定完整总体数据1、2、3、4、5，求均值和总体方差。解释总体方差分母n而非n-1，逐项算离差平方，用E(X²)-E(X)²核对，并解释方差与标准差的区别。",
}


def source_hash():
    paths = sorted((ROOT / "src/manimagent").glob("*.py")) + [
        ROOT / "scripts/test_animation_e2e.py",
        Path(__file__),
        ROOT / "uv.lock",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--workers", type=int, choices=[1, 2], default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--topics", default=",".join(TOPICS))
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
    version = source_hash()

    def run(topic, attempt):
        before = source_hash()
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
                    "scripts.test_animation_e2e",
                    "--output",
                    str(directory),
                    "--question",
                    question,
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
        after = source_hash()
        return {
            **result,
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
