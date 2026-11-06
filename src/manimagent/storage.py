"""Atomic JSON I/O shared by resources, configuration and instance ownership."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

# All stores in one service process share the same atomic JSON publication lock.
# Windows readers can otherwise race os.replace and fail with sharing violations.
_JSON_IO_LOCK = threading.RLock()


def read_json_bytes(path: Path) -> bytes:
    with _JSON_IO_LOCK:
        return path.read_bytes()


@contextmanager
def _atomic_text(path: Path) -> Iterator[TextIO]:
    """Publish a private UTF-8 file only after serialization and flush succeed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        with _JSON_IO_LOCK:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: BaseModel | dict) -> None:
    """Publish private JSON atomically, sharing the Windows reader lock."""
    with _atomic_text(path) as stream:
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        json.dump(data, stream, ensure_ascii=False, indent=2)


def write_text(path: Path, value: str) -> None:
    """Publish a derived Markdown document with the same durability as JSON."""
    with _atomic_text(path) as stream:
        stream.write(value)
