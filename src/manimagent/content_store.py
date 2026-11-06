from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel

from manimagent.errors import ContentError
from manimagent.materials.detection import detect_upload
from manimagent.schemas import FileAsset
from manimagent.storage import read_json_bytes, write_json


def new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


class ContentStore:
    def __init__(self, root: Path, max_upload_bytes: int):
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes
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

    def put_file(self, stream: BinaryIO, filename: str) -> FileAsset:
        # Never trust the submitted MIME type or a client-supplied path.
        name = re.split(r"[/\\]", filename)[-1]
        name = "".join(c for c in name if c.isprintable()).strip()[:180] or "material.txt"
        try:
            return self._write_file(stream, name, max_bytes=self.max_upload_bytes)
        except ContentError as exc:
            error = ContentError(
                exc.error.code, f"{name}：{exc.error.message}", exc.http_status, exc.state
            )
            error.error.details = {**exc.error.details, "filename": name}
            raise error from exc

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
                media_type=media_type or detect_upload(temporary, filename),
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
