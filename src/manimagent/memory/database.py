"""Shared SQLite boundary for entries, source replay and durable learning tasks.

Both stores use the same write transaction. Revision/cancellation fences and the
learning receipt must commit with the entries they protect, never afterwards.
This layer owns no memory policy and never expires or evicts entries.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from manimagent.errors import ContentError

from .models import MemoryEntry


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def expired(entry: MemoryEntry, at: datetime) -> bool:
    return entry.expires_at is not None and datetime.fromisoformat(entry.expires_at) <= at


class MemoryDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO memory_metadata(key,value) VALUES ('manual_revision',0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sources "
                "(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, entry_ids TEXT NOT NULL, "
                "input_fingerprint TEXT NOT NULL)"
            )
            if "input_fingerprint" not in {
                row[1] for row in db.execute("PRAGMA table_info(sources)")
            }:
                db.execute(
                    "ALTER TABLE sources ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(entry_id TEXT NOT NULL, session_id TEXT NOT NULL, last_seen TEXT NOT NULL, "
                "last_order INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY(entry_id, session_id), "
                "FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE)"
            )
            if "last_order" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN last_order INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_learning_jobs (
                job_id TEXT PRIMARY KEY, state TEXT NOT NULL, attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                payload TEXT NOT NULL, summary TEXT NOT NULL)""")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def revision(db: sqlite3.Connection) -> int:
        return db.execute(
            "SELECT value FROM memory_metadata WHERE key='manual_revision'"
        ).fetchone()[0]

    @staticmethod
    def bump_revision(db: sqlite3.Connection) -> None:
        db.execute("UPDATE memory_metadata SET value=value+1 WHERE key='manual_revision'")

    @staticmethod
    def entries(db: sqlite3.Connection) -> list[MemoryEntry]:
        return [
            MemoryEntry.model_validate_json(row[0])
            for row in db.execute("SELECT body FROM entries")
        ]

    @staticmethod
    def require_running(db: sqlite3.Connection, job_id: str) -> None:
        task = db.execute(
            "SELECT state FROM memory_learning_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if task is None or task[0] != "running":
            raise ContentError(
                "memory_learning_cancelled", "后台记忆任务已停止，未写入提取结果", 409
            )

    @staticmethod
    def record_learning_write(db: sqlite3.Connection, job_id: str, effect: dict) -> None:
        payload = json.loads(
            db.execute(
                "SELECT payload FROM memory_learning_jobs WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        )
        payload["write_result"] = effect
        db.execute(
            "UPDATE memory_learning_jobs SET payload=? WHERE job_id=?",
            (json.dumps(payload, ensure_ascii=False), job_id),
        )

    def committed_entries(
        self, db: sqlite3.Connection, source_id: str, at: datetime
    ) -> set[str] | None:
        """None means uncommitted; an empty set is a committed empty/deleted result."""
        row = db.execute(
            "SELECT entry_ids FROM sources WHERE id=?", (fingerprint(source_id),)
        ).fetchone()
        if row is None:
            return None
        ids = set(json.loads(row[0]))
        return {
            entry.memory_id
            for entry in self.entries(db)
            if entry.memory_id in ids and entry.state == "active" and not expired(entry, at)
        }
