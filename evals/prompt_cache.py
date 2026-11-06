"""Opt-in, six-call comparison of legacy and stable-prefix synthetic lesson prompts.

No retries, no user materials, no persisted credentials, no claim of learning benefit.
Use --config for the saved answer-writer profile, or --mimo-env for the fixed MiMo
profile with MIMO_API_KEY read into memory from that dotenv file only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from evals.support import save
from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import ModelConfig, Settings, load_settings
from manimagent.llm.client import ChatProvider, ProviderError
from manimagent.llm.settings import LLMSettingsStore
from manimagent.memory.projection import user_context
from manimagent.schemas import Usage

SYSTEM = (
    "你是一位初中数学教师。输入 JSON 包含教学材料、用户偏好和本次 instruction。"
    "遵循当前问题，参考材料解题；只回答被要求的练习，不复述整份材料。"
    "用中文简述等式变形，写出 x = 整数，并给出代回验算；不超过120个汉字。"
)
VARIANTS = ("legacy", "stable_prefix")


def workload() -> dict:
    """Fifty distinct equations; every solution and substitution is computed exactly."""
    blocks = []
    for index in range(50):
        family, n = divmod(index, 10)
        a, b, x = n + 2, n + 3, n - 4
        if family == 0:
            c = a * x + b
            equation = f"{a}x + {b} = {c}"
            steps = f"两边同时减{b}，得{a}x={c - b}；再同时除以{a}，得x={x}。"
            verification = f"左边={a}×({x})+{b}={a * x + b}，右边={c}。"
            assert a * x + b == c
        elif family == 1:
            c = a * (x + b)
            equation = f"{a}(x + {b}) = {c}"
            steps = f"先同时除以{a}，得x+{b}={x + b}；再同时减{b}，得x={x}。"
            verification = f"左边={a}×({x}+{b})={a * (x + b)}，右边={c}。"
            assert a * (x + b) == c
        elif family == 2:
            d, e = a + 1, b - x
            equation = f"{a}x + {b} = {d}x + ({e})"
            steps = f"同时减{a}x与({e})，得{b}-({e})=x，因此x={x}。"
            verification = f"左边={a}×({x})+{b}={a * x + b}，右边={d}×({x})+({e})={d * x + e}。"
            assert a * x + b == d * x + e
        elif family == 3:
            c, x = n - 3, a * (n - 3) - b
            equation = f"(x + {b}) / {a} = {c}"
            steps = f"两边同时乘{a}，得x+{b}={a * c}；再同时减{b}，得x={x}。"
            verification = f"左边=({x}+{b})/{a}={c}，右边={c}。"
            assert x + b == a * c
        else:
            c = a * x - b
            equation = f"{a}x - {b} = {c}"
            steps = f"两边同时加{b}，得{a}x={c + b}；再同时除以{a}，得x={x}。"
            verification = f"左边={a}×({x})-{b}={a * x - b}，右边={c}。"
            assert a * x - b == c
        blocks.append(
            {
                "block_id": f"exercise_{index + 1:02d}",
                "text": f"练习{index + 1:02d}：{equation}。解法：{steps}验算：{verification}两边相等。",
                "equation": equation,
                "expected_solution": x,
            }
        )
    material_id = "material_synthetic_linear_50"
    shared = {
        "materials": [{"material_id": material_id, "blocks": blocks, "warnings": []}],
        "allowed_source_refs": [
            {"material_id": material_id, "block_id": block["block_id"]} for block in blocks
        ],
    }
    snapshot = {
        "policy_version": 1,
        "captured_at": "2026-11-06T00:00:00+00:00",
        "scope": "algebra",
        "session_id": "synthetic_cache_session",
        "entries": [
            {
                "memory_id": "memory_synthetic_language",
                "field": "profile.language",
                "value": "使用简体中文解释数学步骤",
                "scope": "global",
                "layer": "deep",
                "temporary": False,
                "updated_at": "2026-11-05T00:00:00+00:00",
            },
            {
                "memory_id": "memory_synthetic_method",
                "field": "teaching.method",
                "value": "每次等式变形说明两边做相同运算，并代回原式验算",
                "scope": "algebra",
                "layer": "deep",
                "temporary": False,
                "updated_at": "2026-11-05T00:00:00+00:00",
            },
        ],
    }
    rounds = []
    for round_index, number in enumerate((3, 24, 47), 1):
        rounds.append(
            {
                "round": round_index,
                "instruction": f"请讲解练习{number:02d}，给出解与代回原方程的验算。",
                "block_id": blocks[number - 1]["block_id"],
                "expected_solution": blocks[number - 1]["expected_solution"],
                "snapshot": snapshot,
            }
        )
    return {"synthetic": True, "system": SYSTEM, "shared": shared, "rounds": rounds}


def payload_for(variant: str, shared: dict, item: dict) -> dict:
    if variant == "legacy":
        return {"instruction": item["instruction"], **shared, "user_memory": item["snapshot"]}
    if variant != "stable_prefix":
        raise ValueError("Unknown comparison variant")
    return user_context(
        shared, role="answer_writer", snapshot=item["snapshot"], instruction=item["instruction"]
    )


def solution_matches(response: str | None, expected: int) -> bool | None:
    # The x in 4x = -8 is not an isolated solution; do not misread its RHS.
    answer = re.search(r"(?<![A-Za-z0-9_.])x\s*[=＝]\s*([+-]?\d+)", response or "")
    return int(answer[1]) == expected if answer else None


def render_report(result: dict) -> str:
    rows = []
    for call in result["calls"]:
        usage = call["normalized_usage"] or {}
        input_tokens = usage.get("input_tokens")
        cached_tokens = usage.get("cached_input_tokens")
        rows.append(
            f"| {call['sequence']} | {call['variant']} | {call['round']} | "
            f"{input_tokens if input_tokens is not None else '未知'} | "
            f"{cached_tokens if cached_tokens is not None else '未知'} | "
            f"{call['elapsed_seconds']} | {call['answered']} | {call['error_code'] or '—'} |"
        )
    return "\n".join(
        [
            "# 合成提示词缓存对照",
            "",
            f"运行时间（UTC）：{result['started_at']}。模型：{result['model']}。",
            "",
            "50 条程序构造的一次方程教学材料，每条包含准确解及代回验算，无无关填充。"
            "两方案采用同一 system、材料/ref ID、稳定偏好，交错各调用 3 次；每轮提问不同练习。"
            "旧方案把 instruction 放在前、完整审计 snapshot 放在末；当前方案直接调用 user_context。",
            "",
            "上限：6 次调用、384 输出 token/次、40 秒/次、240 秒总期限；thinking disabled，禁止重试。"
            "供应商缓存状态无法清空或控制：第 1 轮只是首次尝试，第 2–3 轮只是复用尝试，不预先认定冷/热命中。",
            "",
            "| 序号 | 方案 | 轮次 | 输入 token | 已缓存输入 token | 秒 | 完整响应 | 错误 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            f"实际调用数：{result['usage']['model_calls']}；完整响应：{sum(c['answered'] for c in result['calls'])}/6。",
            "",
            "未知不当作零。缓存 token 是完整输入的子集，未从上下文或总 token 预算扣除。"
            "summary.json 按方案保留首轮与复用轮、缓存覆盖数及可统计输入分母；"
            "calls.json 保留全部成功/失败和合成输出，workload.json 可重建输入。",
            "",
            "这是单次、小样本、合成缓存负载。结构投影和顺序调整同时变化，不能分别归因；"
            "共享 system 也可能跨方案命中。无论本次命中与否，都不能据此声称普遍提速、成本下降、"
            "用户学习效果改善或 memory 质量提升。未采集真人数据，未运行视频/TTS。",
            "",
            "报告不包含凭据、HTTP 鉴权头或真实用户材料。配置只读，输出目录必须新建。",
            "",
        ]
    )


def run(settings: Settings, resolver: Callable[[str], str | None], output: Path, *, transport=None):
    output.mkdir(parents=True, exist_ok=False)
    cfg = settings.model_for("answer_writer").model_copy(
        update={"max_output_tokens": 384, "thinking": "disabled"}
    )
    bounded = settings.model_copy(
        deep=True,
        update={
            "models": {"cache_eval": cfg},
            "roles": {"answer_writer": "cache_eval"},
            "task_timeout": 240,
            "model_timeout": 40,
            "max_model_calls": 6,
            "max_total_tokens": 180000,
        },
    )
    data = workload()
    save(output / "workload.json", data)
    events = []

    def trace(event):
        # Never persist full requests, raw upstream errors, headers or credentials.
        if event.get("event") == "call_summary":
            events.append(event)

    provider = ChatProvider(bounded, transport=transport, trace=trace, credential_resolver=resolver)
    budget = Budget(bounded, Usage())
    budget.retry_allowance = 0
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": cfg.model,
        "synthetic": True,
        "limits": {
            "model_calls": 6,
            "output_tokens_per_call": 384,
            "deadline_seconds": 240,
            "call_timeout_seconds": 40,
            "retries": 0,
            "thinking": "disabled",
        },
        "calls": [],
    }
    for item in data["rounds"]:
        for variant in VARIANTS:
            payload = payload_for(variant, data["shared"], item)
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            before, started = len(events), time.monotonic()
            response, error_code = None, None
            try:
                response = provider.complete_text(
                    "answer_writer", SYSTEM, payload, budget, deadline=budget.deadline
                )
            except (ProviderError, BudgetExceeded) as exc:
                error_code = getattr(exc, "code", "budget_exceeded")
            except Exception as exc:
                # Error text may contain upstream payloads; keep only a type label.
                error_code = type(exc).__name__
            summaries = events[before:]
            assert len(summaries) <= 1, "Evaluation retry limit violated"
            summary = summaries[0] if summaries else {}
            call = {
                "sequence": len(result["calls"]) + 1,
                "variant": variant,
                "round": item["round"],
                "exposure": "first_attempt" if item["round"] == 1 else "reuse_attempt",
                "block_id": item["block_id"],
                "expected_solution": item["expected_solution"],
                "answered": response is not None,
                "parsed_solution_matches": solution_matches(response, item["expected_solution"]),
                "answer": response,
                "error_code": error_code,
                "http_status": summary.get("http_status"),
                "elapsed_seconds": summary.get(
                    "elapsed_seconds", round(time.monotonic() - started, 3)
                ),
                "normalized_usage": summary.get("normalized_usage"),
                "attempts": len(summaries),
                "payload_bytes": len(encoded),
                "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            }
            result["calls"].append(call)
            save(output / "calls.json", result["calls"])
            print(
                json.dumps(
                    {
                        key: call[key]
                        for key in (
                            "sequence",
                            "variant",
                            "round",
                            "answered",
                            "error_code",
                            "elapsed_seconds",
                            "normalized_usage",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    result["elapsed_seconds"] = round(time.monotonic() - budget.started, 3)
    result["usage"] = budget.usage.model_dump()
    result["groups"] = {}
    for variant in VARIANTS:
        calls = [call for call in result["calls"] if call["variant"] == variant]
        known = [
            call["normalized_usage"]
            for call in calls
            if call["normalized_usage"] and call["normalized_usage"]["cache_reported"]
        ]
        result["groups"][variant] = {
            "cache_reported_calls": len(known),
            "cached_input_tokens": sum(c["cached_input_tokens"] for c in known) if known else None,
            "cache_accounted_input_tokens": sum(c["cache_accounted_input_tokens"] for c in known),
            "first_attempt_sequence": calls[0]["sequence"],
            "reuse_attempt_sequences": [call["sequence"] for call in calls[1:]],
        }
    paths = [
        Path(__file__),
        *[
            Path(__file__).parents[1] / "src/manimagent" / name
            for name in ("llm/client.py", "llm/usage.py", "memory/projection.py")
        ],
    ]
    result["source_sha256"] = {
        path.relative_to(Path(__file__).parents[1]).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    save(output / "summary.json", result)
    save(output / "报告.md", render_report(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="Read saved answer-writer configuration")
    source.add_argument(
        "--mimo-env", type=Path, help="Read only MIMO_API_KEY from this dotenv file"
    )
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory")
    args = parser.parse_args()
    try:
        if args.mimo_env:
            secret = dotenv_values(args.mimo_env, interpolate=False).get("MIMO_API_KEY")
            if not secret:
                raise ValueError("Missing MiMo credential")
            settings = Settings(
                models={
                    "text": ModelConfig(
                        base_url="https://api.xiaomimimo.com/v1",
                        model="mimo-v2.6-flash",
                        api_key_env="MIMO_API_KEY",
                        auth_mode="api_key_header",
                        api_key_header="api-key",
                    )
                }
            )

            def resolver(reference):
                return secret if reference == "MIMO_API_KEY" else None
        else:
            initial = load_settings(args.config)
            store = LLMSettingsStore(initial, initial.private_dir or initial.runs_dir / "private")
            settings, resolver = store.effective(initial), store.credentials.resolve
        result = run(settings, resolver, args.output)
        return int(not all(call["answered"] for call in result["calls"]))
    except Exception as exc:
        print(json.dumps({"setup_or_report_error": type(exc).__name__}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
