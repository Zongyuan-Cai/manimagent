"""One live temporary observation, then clock-injected retirement checks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from evals.narrated import isolated_settings
from evals.support import save
from manimagent.api import create_app
from manimagent.memory.service import EXTRACTION_INSTRUCTION

ROOT = Path(__file__).resolve().parent
DATABASE_RUN = ROOT.parent / "extraction-control-v2"
PROMPT_SOURCE = ROOT.parents[2] / "src/manimagent/memory/service.py"


def main():
    if (ROOT / "result.json").exists():
        raise FileExistsError("Retain the earlier retirement attempt")
    result = {
        "kind": "supplementary_retirement_control",
        "passed": False,
        "database_run": str(DATABASE_RUN),
        "main_trajectory_replacement": False,
        "prompt_hash": hashlib.sha256(PROMPT_SOURCE.read_bytes()).hexdigest(),
    }
    save(ROOT / "extraction-system-prompt.txt", EXTRACTION_INSTRUCTION)
    clock = [datetime(2026, 11, 11, tzinfo=UTC)]
    try:
        with isolated_settings(None, DATABASE_RUN) as settings:
            settings.tts.enabled = False
            app = create_app(settings)
            memory = app.state.job_manager.memory
            memory.clock = lambda: clock[0]
            with TestClient(app) as client:

                def state():
                    response = client.get("/api/v2/memory")
                    response.raise_for_status()
                    return response.json()

                before = state()
                save(ROOT / "memory-before-observation.json", before)
                deep = [
                    e
                    for e in before["entries"]
                    if e["layer"] == "deep" and e["confirmed_at"] is None
                ]
                request = {
                    "session_id": "retirement_s1",
                    "source_id": "retirement_temp_1",
                    "scope": "global",
                    "text": "仅本次学习，我希望图形中的辅助线用青色表示。",
                }
                save(ROOT / "request.json", request)
                response = client.post("/api/v2/memory/observations", json=request)
                body = response.json()
                save(ROOT / "response.json", {"status_code": response.status_code, "body": body})
                after_observation = state()
                save(ROOT / "memory-after-observation.json", after_observation)
                snapshot_initial = memory.snapshot("retirement_s1")
                save(ROOT / "snapshot-before-advance.json", snapshot_initial)
                clock[0] += timedelta(days=16)
                save(
                    ROOT / "clock.json",
                    {
                        "initial": "2026-11-11T00:00:00+00:00",
                        "now": clock[0].isoformat(),
                        "system_clock_changed": False,
                    },
                )
                before_cleanup = state()
                snapshot_expired = memory.snapshot("retirement_s1")
                save(ROOT / "memory-before-cleanup.json", before_cleanup)
                save(ROOT / "snapshot-before-cleanup.json", snapshot_expired)
                cleaned = client.post("/api/v2/memory/cleanup")
                cleanup_body = cleaned.json()
                save(
                    ROOT / "cleanup-response.json",
                    {"status_code": cleaned.status_code, "body": cleanup_body},
                )
                after_cleanup = state()
                save(ROOT / "memory-after-cleanup.json", after_cleanup)
                snapshot_final = memory.snapshot("retirement_s1")
                save(ROOT / "snapshot-after-cleanup.json", snapshot_final)
                observations = body.get("entries", [])
                deep_ids = {e["memory_id"] for e in deep}
                temp_ids = {e["memory_id"] for e in observations}

                def ids(value):
                    return {e["memory_id"] for e in value["entries"]}

                checks = {
                    "preexisting_naturally_promoted_deep": bool(deep_ids),
                    "real_single_extraction": response.status_code == 200
                    and body.get("usage", {}).get("model_calls") == 1,
                    "temporary_different_field": bool(observations)
                    and all(
                        e["temporary"]
                        and e["layer"] == "short_term"
                        and e["field"] not in {d["field"] for d in deep}
                        for e in observations
                    ),
                    "temporary_used_in_original_session": temp_ids <= ids(snapshot_initial),
                    "expired_entry_still_stored_before_cleanup": bool(temp_ids)
                    and temp_ids <= ids(before_cleanup),
                    "expired_excluded_before_cleanup": bool(temp_ids)
                    and not temp_ids & ids(snapshot_expired),
                    "deep_selected_before_cleanup": deep_ids <= ids(snapshot_expired),
                    "cleanup_count_matches": cleaned.status_code == 200
                    and cleanup_body.get("expired") == len(temp_ids),
                    "expired_deleted": not temp_ids & ids(after_cleanup),
                    "deep_still_stored": deep_ids <= ids(after_cleanup),
                    "deep_still_selected": deep_ids <= ids(snapshot_final),
                    "deep_contents_unchanged": all(e in after_cleanup["entries"] for e in deep),
                }
                result.update(
                    checks=checks,
                    passed=all(checks.values()),
                    usage=body.get("usage"),
                    deep_entry_ids=sorted(deep_ids),
                    temporary_entry_ids=sorted(temp_ids),
                    cleanup=cleanup_body,
                )
    except Exception as exc:
        result["harness_failure_type"] = type(exc).__name__
        raise
    finally:
        save(ROOT / "result.json", result)
        lines = [
            "# 记忆清退补充控制",
            "",
            "复用独立 extraction-control-v2 的自然晋升记忆库；原七步请求、响应和结果均保留。这不替代主二十轮轨迹。",
            "",
            "通过一次真实提取新增本次配色偏好，然后仅推进测试实例 MemoryStore.clock 十六天；没有修改系统时间。",
            "",
            "|检查|结果|",
            "|---|---|",
        ]
        lines += [
            f"|{name}|{'通过' if passed else '失败'}|"
            for name, passed in result.get("checks", {}).items()
        ]
        lines += [
            "",
            f"综合结果：{'通过' if result['passed'] else '未全部通过'}。",
            f"cleanup 返回：`{json.dumps(result.get('cleanup'), ensure_ascii=False)}`。",
            "",
            "snapshot-before-cleanup.json 证明过期条目在物理删除前已不参与检索；memory-before-cleanup.json 和 memory-after-cleanup.json 证明清退效果。",
        ]
        save(ROOT / "报告.md", "\n".join(lines) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
