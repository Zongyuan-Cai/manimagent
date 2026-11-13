"""Supplementary live extraction control, isolated from the twenty-lesson store."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from evals.narrated import isolated_settings
from evals.support import save
from manimagent.api import create_app
from manimagent.memory.service import EXTRACTION_INSTRUCTION

ROOT = Path(__file__).resolve().parent
PROMPT_SOURCE = Path(__file__).resolve().parents[3] / "src/manimagent/memory/service.py"
TEXT = "我偏好每一步推导先说明依据，再写计算。"
CASES = [
    ("first_observation", "s1", "source1", TEXT, 1),
    ("same_session_repeat", "s1", "source2", TEXT, 1),
    ("second_session", "s2", "source3", TEXT, 2),
    ("third_session_promotion", "s3", "source4", TEXT, 3),
    ("same_source_replay", "s3", "source4", TEXT, 3),
    ("task_request_negative", "s4", "source5", "请帮我理解这道题并制作讲解视频。", None),
    (
        "fictional_speaker_negative",
        "s4",
        "source6",
        "题目里的小王说‘我喜欢红色’，这只是虚构人物的台词。请帮我理解这道题并制作讲解视频。",
        None,
    ),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if (ROOT / "result.json").exists() or (ROOT / "runtime").exists():
        raise FileExistsError("Keep the existing control; do not overwrite or reuse its memory")
    result = {
        "kind": "supplementary_real_extraction_control",
        "passed": False,
        "does_not_replace_main_trajectory": True,
        "prompt_hash_before": digest(PROMPT_SOURCE),
        "steps": [],
    }
    save(ROOT / "extraction-system-prompt.txt", EXTRACTION_INSTRUCTION)
    save(ROOT / "memory-service.py", PROMPT_SOURCE.read_text(encoding="utf-8"))
    started = time.monotonic()
    try:
        with isolated_settings(None, ROOT) as settings:
            settings.tts.enabled = False
            result["model"] = settings.model_for("answer_writer").model
            result["extractor_thinking"] = "disabled"
            result["private_dir"] = str(settings.private_dir)
            app = create_app(settings)
            app.state.job_manager.memory.clock = lambda: datetime(2026, 11, 11, tzinfo=UTC)
            anchor_id = None
            with TestClient(app) as client:
                for index, (name, session, source, text, support) in enumerate(CASES, 1):
                    directory = ROOT / f"step-{index:02d}-{name}"
                    request = {
                        "session_id": session,
                        "source_id": source,
                        "text": text,
                        "scope": "global",
                    }
                    before_response = client.get("/api/v2/memory")
                    before_response.raise_for_status()
                    before = before_response.json()
                    save(directory / "request.json", request)
                    save(directory / "memory-before.json", before)
                    response = client.post("/api/v2/memory/observations", json=request)
                    body = response.json()
                    after_response = client.get("/api/v2/memory")
                    after_response.raise_for_status()
                    after = after_response.json()
                    save(
                        directory / "response.json",
                        {"status_code": response.status_code, "body": body},
                    )
                    save(directory / "memory-after.json", after)
                    failures = []
                    if response.status_code != 200:
                        failures.append("observation_api_failed")
                    elif support is None:
                        if body["entries"] or before["entries"] != after["entries"]:
                            failures.append("negative_input_created_or_changed_memory")
                    else:
                        found = body["entries"]
                        if len(found) != 1:
                            failures.append("expected_exactly_one_preference")
                        else:
                            entry = found[0]
                            if anchor_id is None:
                                anchor_id = entry["memory_id"]
                            if entry["memory_id"] != anchor_id:
                                failures.append("repeated_statement_fragmented")
                            if entry["independent_sessions"] != support:
                                failures.append("incorrect_independent_support")
                            if entry["layer"] != ("deep" if support == 3 else "short_term"):
                                failures.append("incorrect_memory_layer")
                            if entry["temporary"] or entry["confirmed_at"] is not None:
                                failures.append("unexpected_temporary_or_manual_confirmation")
                            if any(v["quote"] not in TEXT for v in entry["evidence"]):
                                failures.append("ungrounded_evidence")
                        if name == "same_source_replay":
                            if body["usage"]["model_calls"] != 0:
                                failures.append("replay_called_model")
                            if before["entries"] != after["entries"]:
                                failures.append("replay_mutated_memory")
                    item = {
                        "step": index,
                        "case": name,
                        "status_code": response.status_code,
                        "expected_support": support,
                        "passed": not failures,
                        "failures": failures,
                        "usage": body.get("usage"),
                        "entries": body.get("entries"),
                    }
                    if name == "same_source_replay":
                        item["mechanism_checks"] = {
                            "zero_model_calls": response.status_code == 200
                            and body["usage"]["model_calls"] == 0,
                            "store_unchanged": before["entries"] == after["entries"],
                            "note": "Expected deep state is inherited from the earlier promotion test; it is separate from replay correctness.",
                        }
                    result["steps"].append(item)
                    save(ROOT / "result-in-progress.json", result)
                    print(
                        json.dumps(
                            {k: v for k, v in item.items() if k != "entries"}, ensure_ascii=False
                        ),
                        flush=True,
                    )
            result["passed"] = all(step["passed"] for step in result["steps"])
    except Exception as exc:
        result["harness_failure_type"] = type(exc).__name__
        raise
    finally:
        result["prompt_hash_after"] = digest(PROMPT_SOURCE)
        result["fixed_prompt"] = result["prompt_hash_before"] == result["prompt_hash_after"]
        result["passed"] = result["passed"] and result["fixed_prompt"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save(ROOT / "result.json", result)
        lines = [
            "# 真实抽取补充控制",
            "",
            "这是独立数据库中的补充控制，不替代主二十轮学习轨迹，也不证明主轨迹成功。",
            "",
            f"- 模型：{result.get('model')}，extractor thinking=disabled。",
            f"- 系统提示词固定：{result['fixed_prompt']}。",
            "- 固定合成原句：我偏好每一步推导先说明依据，再写计算。",
            "- 无人工确认、候选注入、语义合并或静默重试。仅调用真实 observations API。",
            "",
            "|步骤|控制|实际支持数|层级|模型调用数|判定|",
            "|---|---|---|---|---|---|",
        ]
        for step in result["steps"]:
            observed = step.get("entries") or []
            lines.append(
                f"|{step['step']}|{step['case']}|{','.join(str(e['independent_sessions']) for e in observed) or '无条目'}|{','.join(e['layer'] for e in observed) or '无条目'}|{(step.get('usage') or {}).get('model_calls', '未知')}|{'通过' if step['passed'] else '失败：' + ','.join(step['failures'])}|"
            )
        lines += [
            "",
            f"综合判定：{'通过' if result['passed'] else '未全部通过'}。运行 {result['elapsed_seconds']} 秒。",
            "",
            "重放机制与晋升结果分开判定：同源重放的零调用及库状态不变见该步骤mechanism_checks；前面未晋升引起的链断言失败不能归因于重放。",
            "",
            "每步目录保留请求、HTTP响应、usage及数据库前后状态，result.json保留全部条目和证据。主轨迹已有失败不能被该结果覆盖。",
        ]
        save(ROOT / "报告.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
