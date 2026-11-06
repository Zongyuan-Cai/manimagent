"""Bounded text tables, JSON and notebook reading without executing user content."""

from __future__ import annotations

import csv
import io
import json

from manimagent.errors import ContentError
from manimagent.materials.blocks import append_bounded, block


def parse_structured(path, media_type, selection, index, file_id, limits):
    text = path.read_text(encoding="utf-8-sig")
    if len(text) > limits["max_parse_chars"]:
        raise ContentError(
            "budget_exceeded", "文件文字超过处理上限，请拆分文件", state="needs_input"
        )
    blocks, issues, used, total = [], [], 0, 0
    unit_kind = "line"
    try:
        if media_type in {"text/csv", "text/tab-separated-values"}:
            delimiter = "\t" if media_type == "text/tab-separated-values" else ","
            for number, row in enumerate(
                csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True), 1
            ):
                total = number
                used = append_bounded(
                    blocks,
                    block(
                        f"第 {number} 行：{json.dumps(row, ensure_ascii=False)}",
                        index,
                        file_id,
                        {"row": number},
                        "table",
                    ),
                    limits,
                    used,
                )
            unit_kind = "row"
        elif media_type == "application/x-ipynb+json":
            notebook = json.loads(text)
            cells = notebook["cells"]
            if not isinstance(cells, list):
                raise ValueError("Invalid notebook cells")
            for number, cell in enumerate(cells, 1):
                total = number
                source = cell.get("source", "")
                source = "".join(source) if isinstance(source, list) else source
                if not isinstance(source, str):
                    raise ValueError("Invalid notebook source")
                if source.strip():
                    used = append_bounded(
                        blocks,
                        block(
                            f"Notebook 单元 {number}（{cell['cell_type']}）\n{source}",
                            index,
                            file_id,
                            {"cell": number},
                        ),
                        limits,
                        used,
                    )
                if cell.get("attachments"):
                    issues.append(f"Notebook 单元 {number} 包含附件图片，请另行提供可读取内容")
                for output_number, output in enumerate(cell.get("outputs", []), 1):
                    data = output.get("data", {})
                    if not isinstance(data, dict):
                        raise ValueError("Invalid notebook MIME data")
                    if any(key.startswith("image/") for key in data):
                        issues.append(f"Notebook 单元 {number} 包含图像输出，需要进一步读取或核对")
                    supported_text = {"text/plain", "text/markdown", "text/html", "text/latex"}
                    if any(
                        key not in supported_text and not key.startswith("image/") for key in data
                    ):
                        issues.append(
                            f"Notebook 单元 {number} 包含未读取的富媒体输出，请另行提供完整文本或图片"
                        )
                    content = (
                        output.get("text")
                        or output.get("traceback")
                        or data.get("text/plain")
                        or data.get("text/markdown")
                        or data.get("text/html")
                        or data.get("text/latex")
                    )
                    if content:
                        content = "".join(content) if isinstance(content, list) else content
                        if not isinstance(content, str):
                            raise ValueError("Invalid notebook output")
                        used = append_bounded(
                            blocks,
                            block(
                                f"Notebook 单元 {number} · 已保存输出 {output_number}\n{content}",
                                index,
                                file_id,
                                {"cell": number, "output": output_number},
                            ),
                            limits,
                            used,
                        )
            unit_kind = "cell"
        else:
            if media_type == "application/json":
                json.loads(text)
            else:
                for line in text.splitlines():
                    if line.strip():
                        json.loads(line)
            # Preserve original number spelling and escapes instead of round-tripping JSON.
            for number, line in enumerate(text.splitlines(), 1):
                total = number
                if line.strip():
                    used = append_bounded(
                        blocks,
                        block(line, index, file_id, {"line_start": number, "line_end": number}),
                        limits,
                        used,
                    )
    except (ValueError, TypeError, KeyError, csv.Error) as exc:
        raise ContentError(
            "invalid_file",
            "表格、JSON 或 Notebook 结构无效，请检查格式或另存为纯文本",
            state="needs_input",
        ) from exc
    return (
        blocks,
        {
            "input_index": index,
            "requested_scope": selection or "full",
            "state": "complete",
            "unit_kind": unit_kind,
            "total_units": total,
            "processed_ranges": [[1, total]] if total else [],
            "missing_ranges": [],
        },
        list(dict.fromkeys(issues)),
    )
