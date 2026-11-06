"""Readable job memory audits from frozen records and actual request traces.

This module never selects memory or changes provider messages. Materials, prompts,
answers, credentials and non-memory request fields are not copied into the audit.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import TypeAdapter

from manimagent.errors import ContentError
from manimagent.schemas import ResourceID
from manimagent.storage import write_text

log = logging.getLogger(__name__)

SELECTION_RULES = [
    "任务接收时冻结适用记忆；复用回答或生成视频时继承该回答的快照。",
    "选取有效且作用范围匹配的条目；临时要求只适用于所属会话。",
    "同字段冲突依次考虑当前会话表达、适用范围和深层偏好，再应用条目/字符预算。",
    "回答与内容检查过滤 visual.*；材料读取和记忆提取不注入历史偏好。",
    "模型投影只保留字段、值、范围及近期条目的临时标记；证据与时间不进入投影。",
]


def _payload(event: dict) -> dict:
    messages = event.get("request", {}).get("messages", [])
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = next(
                (part.get("text") for part in content if part.get("type") == "text"), None
            )
        if not isinstance(content, str):
            return {}
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _entry_decisions(snapshot: dict | None, projection: dict, role: str) -> list[dict]:
    deep = projection.get("user_memory", {})
    deep = deep.get("deep", []) if isinstance(deep, dict) else []
    recent = projection.get("recent_user_memory", [])
    decisions = []
    for entry in (snapshot or {}).get("entries", []):
        expected = {key: entry[key] for key in ("field", "value", "scope")}
        if entry["layer"] != "deep":
            expected["temporary"] = entry.get("temporary", False)
        actual = deep if entry["layer"] == "deep" else recent
        included = isinstance(actual, list) and expected in actual
        reason = (
            "出现在该次请求的记忆段中"
            if included
            else "该角色不读取历史用户记忆"
            if role in {"material_reader", "memory_extractor"}
            else "文字角色过滤视觉偏好"
            if role in {"answer_writer", "answer_checker"} and entry["field"].startswith("visual.")
            else "该次请求未包含这条记忆；以实际请求记录为准"
        )
        decisions.append({"memory_id": entry["memory_id"], "included": included, "reason": reason})
    return decisions


def memory_context(record: dict, *, status: str) -> dict:
    """Extract only the memory sections actually prepared for each provider call."""
    snapshot = record.get("memory_snapshot")
    calls = []
    for event in record.get("calls", []):
        if event.get("event") == "request":
            payload = _payload(event)
            projection = {
                key: payload[key] for key in ("user_memory", "recent_user_memory") if key in payload
            }
            role = event.get("role", "unknown")
            calls.append(
                {
                    "number": len(calls) + 1,
                    "role": role,
                    "attempt": event.get("attempt"),
                    "memory_present": bool(projection),
                    "projection": projection,
                    "entries": _entry_decisions(snapshot, projection, role),
                    "http_status": None,
                    "response_recorded": False,
                }
            )
        elif event.get("event") == "call_summary" and calls:
            calls[-1].update(
                http_status=event.get("http_status"),
                response_recorded=event.get("http_status") is not None,
            )
    return {
        "job_id": record["job_id"],
        "status": status,
        "snapshot_state": "unknown"
        if "memory_snapshot" not in record or record.get("memory_snapshot_known") is False
        else "recorded"
        if snapshot is not None
        else "disabled",
        "memory_snapshot": snapshot,
        "snapshot_source": record.get("memory_snapshot_source", "job_acceptance"),
        "selection_rules": SELECTION_RULES,
        "calls": calls,
        "note": "请求记录证明构造了这些记忆上下文，不证明服务端成功处理、采用偏好或影响了输出。未记录的调用不推测。",
        "export_error": record.get("memory_context_export_error"),
    }


def _json_block(value) -> str:
    # A user memory can itself contain Markdown fences; keep the audit well formed.
    content = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", content)), default=0) + 1)
    return f"{fence}json\n{content}\n{fence}"


def render_memory_context(context: dict) -> str:
    lines = [
        "# 任务记忆注入记录",
        "",
        f"任务：`{context['job_id']}`；状态：`{context['status']}`。",
        "",
        "此文件是自动生成的调试记录，编辑它不会修改记忆或已保存的回答。",
        "",
        "## 冻结快照",
        "",
    ]
    if context["snapshot_state"] == "unknown":
        lines.append("历史记录未保存快照，无法确认当时是否启用了记忆。")
    elif context["snapshot_state"] == "disabled":
        lines.append("本次未启用用户记忆，或继承的回答未使用记忆。")
    else:
        lines.append(_json_block(context["memory_snapshot"]))
    lines.extend(["", "## 选择与注入规则", ""])
    lines.extend(f"- {rule}" for rule in context["selection_rules"])
    if isinstance(context["snapshot_source"], dict):
        lines.extend(["", "快照继承来源：", "", _json_block(context["snapshot_source"])])
    lines.extend(["", "## 实际请求中的记忆", "", context["note"]])
    if not context["calls"]:
        lines.extend(["", "尚无可用请求记录；不把冻结快照视为已发送给模型。"])
    for call in context["calls"]:
        lines.extend(
            [
                "",
                f"### 调用 {call['number']} · {call['role']} · 尝试 {call['attempt']}",
                "",
                f"HTTP 状态：{call['http_status'] if call['response_recorded'] else '未记录'}。",
                "",
                _json_block(call["projection"]) if call["memory_present"] else "此请求没有记忆段。",
                "",
                "快照条目去向：",
                "",
                _json_block(call["entries"]),
            ]
        )
    return "\n".join(lines) + "\n"


def publish_memory_context(root: Path, record: dict, *, status: str) -> None:
    """Best-effort derived file; failures remain visible without undoing generation."""
    try:
        identifier = TypeAdapter(ResourceID).validate_python(record["job_id"])
        path = root / "memory" / "snapshots" / f"{identifier}.md"
        if not path.resolve().is_relative_to(root.resolve()):
            raise ContentError("invalid_reference", "记忆快照路径超出私有目录")
        write_text(path, render_memory_context(memory_context(record, status=status)))
        record.pop("memory_context_export_error", None)
    except (OSError, ValueError, ContentError):
        log.warning("任务记忆快照未能写入，原任务记录仍可读取", exc_info=True)
        record["memory_context_export_error"] = {
            "code": "memory_context_export_failed",
            "message": "记忆注入文件暂未写入，可从任务接口读取并下载记录",
        }
