from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel

from manimagent.config import Settings
from manimagent.schemas import Error, FileAsset

# All stores in one service process share the same atomic JSON publication lock.
# Windows readers can otherwise race os.replace and fail with sharing violations.
_JSON_IO_LOCK = threading.RLock()


def read_json_bytes(path: Path) -> bytes:
    with _JSON_IO_LOCK:
        return path.read_bytes()


def write_json(path: Path, value: BaseModel | dict) -> None:
    """Publish private JSON atomically, sharing the Windows reader lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        with _JSON_IO_LOCK:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


class ContentError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422, state: str = "failed"):
        super().__init__(message)
        self.error = Error(code=code, message=message)
        self.http_status = status
        self.state = state


class ContentStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = (settings.private_dir or settings.runs_dir / "private").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, collection: str, identifier: str, suffix: str = ".json") -> Path:
        if collection not in {
            "files",
            "materials",
            "answers",
            "jobs",
            "generations",
            "records",
            "videos",
            "video_plans",
            "answer_checks",
        }:
            raise ValueError("未知资源集合")
        if not re.fullmatch(r"[a-z]+_[a-f0-9]{32}", identifier):
            raise ContentError("invalid_reference", "资源不存在", 404)
        return self.root / collection / (identifier + suffix)

    def save(self, collection: str, identifier: str, value: BaseModel | dict) -> None:
        write_json(self.path(collection, identifier), value)

    def load(self, collection: str, identifier: str, schema):
        try:
            return schema.model_validate_json(read_json_bytes(self.path(collection, identifier)))
        except FileNotFoundError as exc:
            raise ContentError("invalid_reference", "资源不存在", 404) from exc

    def load_record(self, identifier: str) -> dict:
        try:
            value = json.loads(read_json_bytes(self.path("records", identifier)))
        except FileNotFoundError as exc:
            raise ContentError("invalid_reference", "执行记录不存在", 404) from exc
        if not isinstance(value, dict):
            raise ContentError("invalid_record", "执行记录已损坏", 500)
        return value

    def file_path(self, identifier: str) -> Path:
        return self.path("files", identifier, ".content")

    def put_file(self, stream: BinaryIO, filename: str, media_type: str = "") -> FileAsset:
        # Never trust the submitted MIME type or a client-supplied path.
        name = re.split(r"[/\\]", filename)[-1]
        name = "".join(c for c in name if c.isprintable()).strip()[:180] or "material.txt"
        return self._write_file(stream, name, max_bytes=self.settings.max_upload_bytes)

    def _write_file(
        self,
        stream: BinaryIO,
        filename: str,
        media_type: str | None = None,
        *,
        max_bytes: int | None = None,
    ) -> FileAsset:
        """Stream, checksum and publish one asset; uploads require format detection."""
        identifier = new_id("file")
        path = self.file_path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        size, digest = 0, hashlib.sha256()
        try:
            with temporary.open("xb") as target:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        if media_type is None:
                            raise ContentError(
                                "upload_too_large", "文件超过上传上限，请缩小文件", 413
                            )
                        raise ContentError("invalid_render", "渲染产物超过 256 MiB 限额")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            asset = FileAsset(
                file_id=identifier,
                filename=filename,
                media_type=media_type or self.detect(temporary, filename),
                size_bytes=size,
                sha256=digest.hexdigest(),
            )
            os.replace(temporary, path)
            self.save("files", identifier, asset)
            return asset
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise

    def detect(self, path: Path, filename: str) -> str:
        with path.open("rb") as stream:
            prefix = stream.read(1024)
        if prefix.startswith(b"%PDF-"):
            return "application/pdf"
        if prefix.startswith(b"PK"):
            try:
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                    if {"[Content_Types].xml", "word/document.xml"} <= names:
                        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            except zipfile.BadZipFile:
                pass
            raise ContentError(
                "unsupported_file_input", "只接受 TXT、Markdown、PDF、DOCX 文件", 415
            )
        if Path(filename).suffix.lower() not in {".txt", ".md", ".markdown", ".pdf", ".docx"}:
            raise ContentError(
                "unsupported_file_input", "只接受 TXT、Markdown、PDF、DOCX 文件", 415
            )
        if Path(filename).suffix.lower() in {".pdf", ".docx"}:
            raise ContentError("invalid_file", "文件特征与 PDF/DOCX 格式不匹配", 415)
        try:
            text = path.read_bytes().decode("utf-8-sig")
            if "\x00" in text or any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                raise UnicodeError()
        except UnicodeError as exc:
            raise ContentError("unsupported_encoding", "文本文件必须使用 UTF-8 编码", 415) from exc
        return "text/markdown" if filename.lower().endswith((".md", ".markdown")) else "text/plain"

    def diagnostic(self, value: dict | str) -> str:
        # Internal assets are registered separately from the upload whitelist.
        raw = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, dict) else value
        return self._write_file(
            io.BytesIO(raw.encode("utf-8")),
            "diagnostic.json" if isinstance(value, dict) else "candidate.md",
            "application/json" if isinstance(value, dict) else "text/markdown",
        ).file_id

    def generated_file(self, source: Path, filename: str, media_type: str) -> FileAsset:
        """Register trusted renderer output, separate from the user upload whitelist."""
        if not source.is_file() or not 0 < source.stat().st_size <= 256 * 1024 * 1024:
            raise ContentError("invalid_render", "渲染产物不存在或超过 256 MiB 限额")
        with source.open("rb") as stream:
            return self._write_file(stream, filename, media_type, max_bytes=256 * 1024 * 1024)

    def verify_file(self, identifier: str) -> FileAsset:
        asset = self.load("files", identifier, FileAsset)
        path = self.file_path(identifier)
        if not path.is_file():
            raise ContentError("invalid_reference", "文件内容已丢失，请重新上传", 404)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != asset.size_bytes or digest != asset.sha256:
            raise ContentError(
                "source_incomplete", "原件完整性检查失败，请重新上传", 422, "needs_input"
            )
        return asset

    def delete_unpublished(self, collection: str, identifier: str) -> None:
        self.path(collection, identifier).unlink(missing_ok=True)
