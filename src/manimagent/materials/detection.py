"""Detect supported upload content without trusting the browser MIME header."""

import zipfile
from pathlib import Path

from manimagent.errors import ContentError

from .capabilities import DOCX, PPTX, UPLOAD_EXTENSIONS, XLSX


def detect_upload(path: Path, filename: str) -> str:
    with path.open("rb") as stream:
        prefix = stream.read(1024)
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    image_type = (
        "image/png"
        if prefix.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if prefix.startswith(b"\xff\xd8\xff")
        else "image/webp"
        if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"
        else "image/gif"
        if prefix.startswith((b"GIF87a", b"GIF89a"))
        else "image/bmp"
        if prefix.startswith(b"BM")
        else "image/tiff"
        if prefix.startswith((b"II\x2a\x00", b"MM\x00\x2a"))
        else None
    )
    if image_type:
        return image_type
    if prefix.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if {"[Content_Types].xml", "word/document.xml"} <= names:
                    return DOCX
                if {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                    return XLSX
                if {"[Content_Types].xml", "ppt/presentation.xml"} <= names:
                    return PPTX
        except zipfile.BadZipFile:
            pass
        raise ContentError(
            "unsupported_file_input",
            "不支持此压缩容器，请解压后选择材料，或另存为 DOCX、XLSX、PPTX",
            415,
        )
    if Path(filename).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ContentError(
            "unsupported_file_input",
            "暂不支持此格式，请查看上传支持范围，或将需要的内容另存为文本",
            415,
        )
    if Path(filename).suffix.lower() in {
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
    }:
        raise ContentError("invalid_file", "文件内容与标注的文档格式不匹配，请检查原件", 415)
    try:
        text = path.read_bytes().decode("utf-8-sig")
        if "\x00" in text or any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
            raise UnicodeError()
    except UnicodeError as exc:
        raise ContentError("unsupported_encoding", "文本文件必须使用 UTF-8 编码", 415) from exc
    suffix = Path(filename).suffix.lower()
    return {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".ndjson": "application/x-ndjson",
        ".ipynb": "application/x-ipynb+json",
    }.get(suffix, "text/plain")
