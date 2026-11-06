"""Shared material blocks, exact text boundaries and character limits."""

from __future__ import annotations

import re

from manimagent.content_store import new_id
from manimagent.errors import ContentError
from manimagent.schemas import MaterialBlock, SourceLocation


def coverage_for(index: int, selection: dict | None) -> dict:
    return {
        "input_index": index,
        "requested_scope": selection or "full",
        "state": "complete",
        "processed_ranges": [],
        "missing_ranges": [],
    }


def text_blocks(
    text: str, index: int, file_id: str | None = None, locator: dict | None = None
) -> list[MaterialBlock]:
    blocks = []
    # Paragraph boundaries retain line numbers; the original input is saved separately.
    for match in re.finditer(r"[^\S\n]*\S[^\n]*(?:\n(?!\s*\n)[^\n]+)*", text):
        value = match.group()
        source = locator or {
            "line_start": text.count("\n", 0, match.start()) + 1,
            "line_end": text.count("\n", 0, match.end()) + 1,
        }
        blocks.append(
            block(value, index, file_id, source, "heading" if value.startswith("#") else "text")
        )
    return blocks


def block(content, index, file_id, locator, kind="text"):
    return MaterialBlock(
        block_id=new_id("block"),
        kind=kind,
        content=content,
        source=SourceLocation(input_index=index, file_id=file_id, locator=locator),
    )


def append_bounded(blocks, item, limits, used):
    used += len(item.content)
    if used > limits["max_parse_chars"]:
        raise ContentError(
            "budget_exceeded", "材料文字超过处理上限，请缩小范围", state="needs_input"
        )
    blocks.append(item)
    return used
