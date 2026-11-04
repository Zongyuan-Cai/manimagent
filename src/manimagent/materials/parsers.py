"""Pure local format parsers; this module runs inside the disposable file worker."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from manimagent.content_store import new_id
from manimagent.errors import ContentError
from manimagent.schemas import MaterialBlock, SourceLocation

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _coverage(index: int, selection: dict | None) -> dict:
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
    for match in re.finditer(r"\S[^\n]*(?:\n(?!\s*\n)[^\n]+)*", text):
        value = match.group()
        source = locator or {
            "line_start": text.count("\n", 0, match.start()) + 1,
            "line_end": text.count("\n", 0, match.end()) + 1,
        }
        blocks.append(
            MaterialBlock(
                block_id=new_id("block"),
                kind="heading" if value.startswith("#") else "text",
                content=value,
                source=SourceLocation(input_index=index, file_id=file_id, locator=source),
            )
        )
    return blocks


def parse_text(path: Path, selection: dict | None, index: int, file_id: str):
    coverage = _coverage(index, selection)
    text = path.read_text(encoding="utf-8-sig")
    blocks = text_blocks(text, index, file_id)
    coverage.update(
        unit_kind="line",
        total_units=len(text.splitlines()),
        processed_ranges=[[1, max(1, len(text.splitlines()))]],
    )
    return blocks, coverage, []


def parse_pdf(path: Path, selection: dict | None, index: int, file_id: str, limits: dict):
    from pypdf import PdfReader

    blocks, issues = [], []
    coverage = _coverage(index, selection)
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
        if not text.strip():
            coverage["missing_ranges"].append([number, number])
            issues.append(f"第 {number} 页没有可读取文字（空页或扫描页），请提供文本版或缩小范围")
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
        if contents is not None and any(op in graphic_ops for _, op in contents.operations):
            issues.append(f"第 {number} 页包含图像或绘图，当前纯文字解析无法核对其内容")
        if "\ufffd" in text or any(0xE000 <= ord(c) <= 0xF8FF for c in text):
            issues.append(f"第 {number} 页存在无法可靠映射的文字或公式符号")
        blocks.extend(text_blocks(text, index, file_id, {"page": number}))
    return blocks, coverage, issues


def parse_docx(path: Path, selection: dict | None, index: int, file_id: str, limits: dict):
    from docx import Document
    from docx.table import Table

    blocks, issues = [], []
    coverage = _coverage(index, selection)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (
            len(entries) > limits["max_archive_entries"]
            or sum(entry.file_size for entry in entries) > limits["max_archive_bytes"]
        ):
            raise ContentError(
                "budget_exceeded", "DOCX 解压规模超限，请缩小文件", state="needs_input"
            )
        for entry in entries:
            if ".." in PurePosixPath(entry.filename).parts or entry.filename.startswith(
                ("/", "\\")
            ):
                raise ContentError("invalid_file", "DOCX 包含非法容器路径", state="needs_input")
        extra_parts = ("word/header", "word/footer", "word/footnotes", "word/endnotes")
        if any(name.startswith(extra_parts) for name in archive.namelist()):
            issues.append("文档包含页眉、页脚或脚注，请核对并将必要内容粘贴为文本")
    document = Document(path)
    root = document.element
    if root.xpath(
        ".//m:oMath | .//w:drawing | .//w:object | .//w:pict | .//w:txbxContent | .//w:altChunk | .//w:ins | .//w:del | .//w:sdt"
    ):
        issues.append(
            "DOCX 包含公式对象、图片、嵌入对象或特殊内容，当前解析不能保证完整，请提供文本版"
        )
    # Paragraph.text drops math-relevant run formatting and legacy symbol glyphs.
    # Do not let a model guess whether flattened 'x2' meant x squared or 2*x.
    if root.xpath(".//w:vertAlign[@w:val='superscript' or @w:val='subscript'] | .//w:sym"):
        issues.append("DOCX 包含上下标或特殊符号，纯文本会丢失数学含义，请提供明确的文本或 LaTeX")
    # Formatting can be inherited from a used character/paragraph/table style.
    # Ignore unused styles: a template may contain many unrelated math styles.
    from docx.oxml.ns import qn

    if document.styles.element.xpath(
        "./w:docDefaults/w:rPrDefault/w:rPr/w:vertAlign[@w:val='superscript' or @w:val='subscript']"
    ):
        issues.append("DOCX 文档默认格式包含上下标，请提供明确的文本或 LaTeX")
    styles = {style.style_id: style for style in document.styles}
    pending = list(root.xpath(".//w:rStyle/@w:val | .//w:pStyle/@w:val | .//w:tblStyle/@w:val"))
    pending.extend(
        style.get(qn("w:styleId"))
        for style in document.styles.element.xpath(
            "./w:style[@w:default='1' or @w:default='true' or @w:default='on']"
        )
    )
    visited = set()
    while pending:
        style_id = pending.pop()
        if style_id in visited or style_id not in styles:
            continue
        visited.add(style_id)
        style = styles[style_id]
        if style.element.xpath(".//w:vertAlign[@w:val='superscript' or @w:val='subscript']"):
            issues.append("DOCX 使用的样式包含上下标，纯文本会丢失数学含义，请提供文本或 LaTeX")
            break
        pending.extend(style.element.xpath("./w:basedOn/@w:val"))
    paragraph = 0
    units = 0
    for item in document.iter_inner_content():
        units += 1
        if isinstance(item, Table):
            if item._element.xpath(".//w:tbl"):
                issues.append("DOCX 包含嵌套表格，请转为普通表格或文本后提交")
            rows = [
                [cell.text.replace("|", "\\|").replace("\n", "<br>") for cell in row.cells]
                for row in item.rows
            ]
            if not rows:
                continue
            width = max(map(len, rows))
            rows = [row + [""] * (width - len(row)) for row in rows]
            lines = ["| " + " | ".join(row) + " |" for row in rows]
            lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
            blocks.append(
                MaterialBlock(
                    block_id=new_id("block"),
                    kind="table",
                    content="\n".join(lines),
                    source=SourceLocation(
                        input_index=index,
                        file_id=file_id,
                        locator=None,
                        verification="unavailable",
                    ),
                )
            )
        else:
            paragraph += 1
            if item.text.strip():
                blocks.append(
                    MaterialBlock(
                        block_id=new_id("block"),
                        kind="heading"
                        if item.style and item.style.name.startswith("Heading")
                        else "text",
                        content=item.text,
                        source=SourceLocation(
                            input_index=index, file_id=file_id, locator={"paragraph": paragraph}
                        ),
                    )
                )
    coverage.update(
        unit_kind="body_item", total_units=units, processed_ranges=[[1, units]] if units else []
    )
    return blocks, coverage, issues


def parse_file(
    path: Path, media_type: str, selection: dict | None, index: int, file_id: str, limits: dict
) -> dict:
    if media_type.startswith("text/"):
        blocks, coverage, issues = parse_text(path, selection, index, file_id)
    elif media_type == "application/pdf":
        blocks, coverage, issues = parse_pdf(path, selection, index, file_id, limits)
    elif media_type == DOCX:
        blocks, coverage, issues = parse_docx(path, selection, index, file_id, limits)
    else:
        raise ContentError("unsupported_file_input", "尚未实现此格式解析", 415)
    if not blocks:
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
    }
