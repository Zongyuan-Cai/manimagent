"""OS-held, crash-released ownership of one private data directory."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from manimagent.content_store import ContentError


class InstanceLease:
    def __init__(self, root: Path):
        self.root = root
        self.stream = None
        self.identifier = uuid.uuid4().hex

    def acquire(self):
        stream = (self.root / "instance.lock").open("a+b")
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise ContentError(
                "instance_busy", "此数据目录已有服务或命令正在运行，请使用现有服务", 409
            ) from exc
        self.stream = stream
        try:
            self.publish()
        except BaseException:
            self.release()
            raise

    def publish(self, url: str | None = None):
        if self.stream is None:
            raise RuntimeError("Data directory is not owned")
        path = self.root / "instance.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"id": self.identifier, "pid": os.getpid(), "url": url}), encoding="utf-8"
        )
        os.replace(temporary, path)

    def release(self):
        if self.stream is None:
            return
        stream, self.stream = self.stream, None
        try:
            (self.root / "instance.json").unlink(missing_ok=True)
        finally:
            stream.close()
