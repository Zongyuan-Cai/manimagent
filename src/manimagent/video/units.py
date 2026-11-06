"""Lossless teaching-unit ranges from saved Markdown; no model calls or rendering."""

from __future__ import annotations

import re

from manimagent.errors import ContentError
from manimagent.video.models import TeachingUnit, digest


def teaching_units(markdown: str) -> list[TeachingUnit]:
    """Unicode code-point spans; never split inside a fenced block or display formula."""
    spans, start, offset, fence, display = [], 0, 0, None, None
    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r" {0,3}(`{3,}|~{3,})([^\r\n]*)", line)
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                # Backtick info strings cannot themselves contain backticks.
                if token[0] != "`" or "`" not in suffix:
                    fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not suffix.strip():
                fence = None
        if fence is None:
            if stripped in {"$$", r"\[", r"\]"}:
                if stripped == display or (display == r"\[" and stripped == r"\]"):
                    display = None
                elif display is None:
                    display = stripped
        end = offset + len(line)
        if not stripped and fence is None and display is None:
            if markdown[start:offset].strip():
                spans.append((start, offset))
            start = end
        offset = end
    if markdown[start:].strip():
        spans.append((start, len(markdown)))
    # Headings carry context, rather than becoming spurious standalone teaching tasks.
    merged = []
    pending = None
    for start, end in spans:
        if re.fullmatch(r"#{1,6}\s+[^\n]+", markdown[start:end].strip()):
            pending = start if pending is None else pending
            continue
        merged.append((start if pending is None else pending, end))
        pending = None
    if pending is not None:
        merged.append((pending, len(markdown)))
    spans = merged
    if len(spans) > 42:
        # Writers often put every display equation in a separate paragraph.
        # Pack adjacent short paragraphs within a section, preserving exact spans;
        # paragraph formatting alone must not turn an ordinary problem into >70 units.
        spans = []
        for start, end in merged:
            if (
                spans
                and not re.match(r"#{1,6}\s", markdown[start:end].lstrip())
                and end - spans[-1][0] <= 300
            ):
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
    if not spans or len(spans) > 70:
        raise ContentError(
            "video_scope", "正文教学单元过多或为空，请拆分为独立主题", state="needs_input"
        )
    return [
        TeachingUnit(
            unit_id=f"u{index:03d}",
            start=start,
            end=end,
            text=markdown[start:end],
            sha256=digest(markdown[start:end]),
        )
        for index, (start, end) in enumerate(spans, 1)
    ]
