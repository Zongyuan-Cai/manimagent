"""Local parsers. File parsing runs in a disposable process with a wall deadline."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from manimagent.budget import Budget
from manimagent.content_store import ContentError, ContentStore, new_id
from manimagent.schemas import (
    FileInput,
    MaterialBlock,
    MaterialIR,
    ParseRequest,
    SourceLocation,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


def parse_file(
    path: Path, media_type: str, selection: dict | None, index: int, file_id: str, limits: dict
) -> dict:
    blocks, issues = [], []
    coverage = {
        "input_index": index,
        "requested_scope": selection or "full",
        "state": "complete",
        "processed_ranges": [],
        "missing_ranges": [],
    }
    if media_type.startswith("text/"):
        text = path.read_text(encoding="utf-8-sig")
        blocks = text_blocks(text, index, file_id)
        coverage.update(
            unit_kind="line",
            total_units=len(text.splitlines()),
            processed_ranges=[[1, max(1, len(text.splitlines()))]],
        )
    elif media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ContentError(
                "source_incomplete", "PDF 已加密，请上传未加密原件", state="needs_input"
            )
        total = len(reader.pages)
        start, end = (selection["start"], selection["end"]) if selection else (1, total)
        if not total or end > total or start > total:
            raise ContentError(
                "invalid_selection", "页码范围超出 PDF 实际页数", state="needs_input"
            )
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
                issues.append(
                    f"第 {number} 页没有可读取文字（空页或扫描页），请提供文本版或缩小范围"
                )
            # Vector diagrams and raster images cannot silently disappear from a math problem.
            graphic_ops = {b"Do", b"INLINE IMAGE", b"sh", b"S", b"s", b"f", b"f*", b"B", b"B*"}
            if contents is not None and any(op in graphic_ops for _, op in contents.operations):
                issues.append(f"第 {number} 页包含图像或绘图，当前纯文字解析无法核对其内容")
            if "\ufffd" in text or any(0xE000 <= ord(c) <= 0xF8FF for c in text):
                issues.append(f"第 {number} 页存在无法可靠映射的文字或公式符号")
            blocks.extend(text_blocks(text, index, file_id, {"page": number}))
    elif media_type == DOCX:
        from docx import Document
        from docx.table import Table

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


class MaterialService:
    def __init__(self, store: ContentStore):
        self.store = store
        self.settings = store.settings

    def validate(self, request: ParseRequest) -> None:
        for item in request.inputs:
            if isinstance(item, FileInput):
                asset = self.store.verify_file(item.file_id)
                if request.input_mode == "native":
                    raise ContentError(
                        "unsupported_file_input", "尚未实现原生文件读取，请使用 auto 或 local", 422
                    )
                if item.selection and (
                    asset.media_type != "application/pdf" or item.selection.kind != "pages"
                ):
                    raise ContentError("invalid_selection", "当前仅 PDF 支持 pages 范围选择")

    def parse(self, request: ParseRequest, budget: Budget, record: dict) -> MaterialIR:
        self.validate(request)
        blocks, files = [], []
        record["parse"] = {
            "request": request.model_dump(),
            "parser_version": "local-1",
            "coverage": [],
        }
        for index, item in enumerate(request.inputs):
            budget.remaining()
            if item.type == "text":
                blocks.extend(text_blocks(item.text, index))
                record["parse"]["coverage"].append(
                    {
                        "input_index": index,
                        "state": "complete",
                        "unit_kind": "line",
                        "total_units": len(item.text.splitlines()),
                        "processed_ranges": [[1, len(item.text.splitlines())]],
                        "missing_ranges": [],
                    }
                )
                continue
            asset = self.store.verify_file(item.file_id)
            files.append(item.file_id)
            limits = {
                name: getattr(self.settings, name)
                for name in (
                    "max_parse_pages",
                    "max_parse_chars",
                    "max_archive_entries",
                    "max_archive_bytes",
                )
            }
            with tempfile.TemporaryDirectory(prefix="parse-", dir=self.store.root) as directory:
                task = Path(directory) / "input.json"
                output = Path(directory) / "output.json"
                task.write_text(
                    json.dumps(
                        {
                            "path": str(self.store.file_path(item.file_id)),
                            "media_type": asset.media_type,
                            "selection": item.selection.model_dump() if item.selection else None,
                            "index": index,
                            "file_id": item.file_id,
                            "limits": limits,
                        }
                    ),
                    encoding="utf-8",
                )
                with (Path(directory) / "stderr.log").open("wb") as stderr:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "manimagent.parse_worker", str(task), str(output)],
                        stdout=subprocess.DEVNULL,
                        stderr=stderr,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    deadline = time.monotonic() + min(
                        self.settings.parse_timeout, budget.remaining()
                    )
                    try:
                        while process.poll() is None:
                            budget.remaining(deadline)
                            time.sleep(0.02)
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                if not output.exists():
                    raise ContentError(
                        "invalid_file", "文件解析失败，请检查原件是否损坏", state="needs_input"
                    )
                candidate = json.loads(output.read_text(encoding="utf-8"))
                if "error" in candidate:
                    raise ContentError(**candidate["error"])
            record["parse"]["coverage"].append(candidate["coverage"])
            record["parse"].setdefault("candidates", []).append(candidate)
            if not candidate["blocks"] and asset.media_type.startswith("text/"):
                raise ContentError(
                    "source_incomplete", "文件没有可用正文，请提供完整文字材料", state="needs_input"
                )
            if candidate["issues"]:
                raise ContentError(
                    "content_uncertain", "；".join(candidate["issues"]), state="needs_review"
                )
            blocks.extend(MaterialBlock.model_validate(block) for block in candidate["blocks"])
        if not blocks:
            raise ContentError(
                "source_incomplete", "材料中没有正文，请补充输入", state="needs_input"
            )
        if sum(len(b.content) for b in blocks) > self.settings.max_parse_chars:
            raise ContentError("budget_exceeded", "材料总量超限，请缩小范围", state="needs_input")
        result = MaterialIR(
            material_id=new_id("material"),
            source_file_ids=list(dict.fromkeys(files)),
            blocks=blocks,
        )
        budget.remaining()
        self.store.save("materials", result.material_id, result)
        return result
