"""Public dispatch for the disposable parser worker; formats live in separate modules."""

from __future__ import annotations

from pathlib import Path

from manimagent.errors import ContentError

from .blocks import coverage_for, text_blocks
from .capabilities import DOCX, PPTX, XLSX
from .docx import parse_docx
from .pdf import parse_pdf


def parse_file(
    path: Path, media_type: str, selection: dict | None, index: int, file_id: str, limits: dict
) -> dict:
    visuals = []
    if media_type in {
        "text/csv",
        "text/tab-separated-values",
        "application/json",
        "application/x-ndjson",
        "application/x-ipynb+json",
    }:
        from manimagent.materials.structured import parse_structured

        blocks, coverage, issues = parse_structured(
            path, media_type, selection, index, file_id, limits
        )
    elif media_type == XLSX:
        from manimagent.materials.office import parse_xlsx

        blocks, coverage, issues = parse_xlsx(path, selection, index, file_id, limits)
    elif media_type == PPTX:
        from manimagent.materials.office import parse_pptx

        blocks, coverage, issues, visuals = parse_pptx(path, selection, index, file_id, limits)
    elif media_type.startswith("text/"):
        text = path.read_text(encoding="utf-8-sig")
        blocks, issues = text_blocks(text, index, file_id), []
        coverage = coverage_for(index, selection)
        line_count = len(text.splitlines())
        coverage.update(
            unit_kind="line", total_units=line_count, processed_ranges=[[1, max(1, line_count)]]
        )
    elif media_type == "application/pdf":
        blocks, coverage, issues, visuals = parse_pdf(path, selection, index, file_id, limits)
    elif media_type.startswith("image/"):
        from manimagent.materials.visual_assets import image_visual

        blocks, issues = [], []
        coverage = coverage_for(index, selection)
        coverage.update(unit_kind="image", total_units=1, processed_ranges=[[1, 1]])
        visuals = [image_visual(path.read_bytes(), {"image": 1}, "图片")]
    elif media_type == DOCX:
        blocks, coverage, issues, visuals = parse_docx(path, selection, index, file_id, limits)
    else:
        raise ContentError("unsupported_file_input", "尚未实现此格式解析", 415)
    if not blocks and not visuals:
        issues.append("文件没有可用正文，请提供完整文本材料")
    if sum(len(block.content) for block in blocks) > limits["max_parse_chars"]:
        raise ContentError(
            "budget_exceeded", "材料文字超过处理上限，请缩小范围", state="needs_input"
        )
    if issues:
        coverage["state"] = "incomplete"
    return {
        "blocks": [block.model_dump() for block in blocks],
        "coverage": coverage,
        "issues": issues,
        "visuals": visuals,
    }
