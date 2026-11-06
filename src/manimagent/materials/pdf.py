"""Read selected PDF pages, retaining graphics through bounded visual preparation."""

from __future__ import annotations

from pathlib import Path

from manimagent.errors import ContentError

from .blocks import coverage_for, text_blocks


def parse_pdf(path: Path, selection: dict | None, index: int, file_id: str, limits: dict):
    from pypdf import PdfReader

    from manimagent.materials.visual_assets import pdf_visual

    blocks, issues, visuals = [], [], []
    coverage = coverage_for(index, selection)
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ContentError("source_incomplete", "PDF 已加密，请上传未加密原件", state="needs_input")
    total = len(reader.pages)
    start, end = (selection["start"], selection["end"]) if selection else (1, total)
    if not total or end > total or start > total:
        raise ContentError("invalid_selection", "页码范围超出 PDF 实际页数", state="needs_input")
    if end - start + 1 > limits["max_parse_pages"]:
        raise ContentError(
            "budget_exceeded", "PDF 处理页数超限，请指定更小页码范围", state="needs_input"
        )
    coverage.update(unit_kind="page", total_units=total, processed_ranges=[[start, end]])
    for number in range(start, end + 1):
        page = reader.pages[number - 1]
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > limits["max_archive_bytes"]:
            raise ContentError(
                "budget_exceeded", "PDF 页面展开内容过大，请缩小文件", state="needs_input"
            )
        text = page.extract_text() or ""
        # Vector diagrams and raster images cannot silently disappear from a math problem.
        graphic_ops = {
            b"Do",
            b"INLINE IMAGE",
            b"sh",
            b"S",
            b"s",
            b"f",
            b"F",
            b"f*",
            b"B",
            b"B*",
            b"b",
            b"b*",
        }
        has_graphics = contents is not None and any(
            op in graphic_ops for _, op in contents.operations
        )
        broken_text = "\ufffd" in text or any(0xE000 <= ord(c) <= 0xF8FF for c in text)
        if has_graphics or broken_text:
            if len(visuals) >= limits["max_visual_pages"]:
                raise ContentError(
                    "budget_exceeded",
                    "需要视觉读取的 PDF 页数超限，请缩小页码范围",
                    state="needs_input",
                )
            visuals.append(pdf_visual(path, number))
        elif not text.strip():
            coverage["missing_ranges"].append([number, number])
            issues.append(f"第 {number} 页为空页或没有可读取内容，请核对原件或缩小范围")
        else:
            blocks.extend(text_blocks(text, index, file_id, {"page": number}))
    return blocks, coverage, issues, visuals
