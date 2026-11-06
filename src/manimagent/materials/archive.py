"""Check Office package limits and member paths before parsing XML or media."""

from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from manimagent.errors import ContentError


def inspect_archive(path, limits):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (
            len(entries) > limits["max_archive_entries"]
            or sum(e.file_size for e in entries) > limits["max_archive_bytes"]
        ):
            raise ContentError(
                "budget_exceeded", "Office 文档展开规模超限，请拆分文件", state="needs_input"
            )
        if any(
            ".." in PurePosixPath(e.filename).parts or e.filename.startswith(("/", "\\"))
            for e in entries
        ):
            raise ContentError("invalid_file", "Office 文档包含非法容器路径", state="needs_input")
        return {e.filename for e in entries}
