"""One SQLite transaction boundary for memory, ordering, evidence and receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from manimagent.errors import ContentError
from manimagent.instance import InstanceLease
from manimagent.storage import write_json

from .models import MemoryEntry

SCHEMA_VERSION = 3


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def expired(entry: MemoryEntry, at: datetime) -> bool:
    return entry.expires_at is not None and datetime.fromisoformat(entry.expires_at) <= at


class MemoryDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lease = None
        if self._needs_migration():
            lease = InstanceLease(self.path.parent)
            lease.acquire()
        try:
            self._initialize(migration_owned=lease is not None)
        finally:
            if lease is not None:
                lease.release()

    def _needs_migration(self):
        if not self.path.exists():
            return False
        # Read-only inspection must not initialize or alter a live legacy database.
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = (
                db.execute(
                    "SELECT value FROM memory_metadata WHERE key='schema_version'"
                ).fetchone()
                if "memory_metadata" in tables
                else None
            )
            return "entries" in tables and not version

    def _initialize(self, *, migration_owned):
        with self.transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_metadata (key TEXT PRIMARY KEY,value INTEGER NOT NULL)"
            )
            version = db.execute(
                "SELECT value FROM memory_metadata WHERE key='schema_version'"
            ).fetchone()
            if version and version[0] != SCHEMA_VERSION:
                raise ContentError(
                    "memory_schema_version", "记忆数据库版本不受支持，未修改数据", 409
                )
            migrated = not version and "entries" in tables
            if migrated:
                if not migration_owned:
                    raise ContentError(
                        "memory_migration_lock",
                        "旧数据库在初始化期间发生变化，请停止其他服务后重试迁移",
                        409,
                    )
                # Archive losslessly before removing incompatible active data. No semantic guessing.
                report = {
                    "from_schema": 2,
                    "to_schema": SCHEMA_VERSION,
                    "status": "requires_review",
                    "reason": "旧自由字段、临时条目及提取任务不自动转换",
                    "tables": {
                        name: [
                            dict(zip([d[0] for d in cursor.description], row, strict=True))
                            for row in cursor
                        ]
                        for name in ("entries", "sources", "sessions", "memory_learning_jobs")
                        if name in tables
                        for cursor in [db.execute("SELECT * FROM " + name)]
                    },
                }
                write_json(self.path.with_name("memory-migration-v3.json"), report)
                if "sessions" in tables:
                    db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM entries")
                if "memory_learning_jobs" in tables:
                    db.execute(
                        "UPDATE memory_learning_jobs SET state='cancelled',payload='{}',summary=? WHERE state IN ('waiting','queued','running','failed')",
                        (
                            json.dumps(
                                {
                                    "error": {
                                        "code": "memory_schema_migrated",
                                        "message": "旧提取任务已隔离，请重新提交用户请求",
                                    }
                                }
                            ),
                        ),
                    )
            for key in ("manual_revision", "accepted_sequence"):
                db.execute("INSERT OR IGNORE INTO memory_metadata VALUES (?,0)", (key,))
            db.execute(
                "INSERT OR IGNORE INTO memory_metadata VALUES ('schema_version',?)",
                (SCHEMA_VERSION,),
            )
            if migrated:
                self.bump_revision(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY,body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,entry_ids TEXT NOT NULL,input_fingerprint TEXT NOT NULL)"
            )
            if "input_fingerprint" not in {r[1] for r in db.execute("PRAGMA table_info(sources)")}:
                db.execute(
                    "ALTER TABLE sources ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            db.execute("""CREATE TABLE IF NOT EXISTS acceptances (
                source_hash TEXT PRIMARY KEY,sequence INTEGER NOT NULL UNIQUE,signature TEXT NOT NULL,
                snapshot TEXT,accepted_at TEXT NOT NULL,withdrawn INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY,entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                source_hash TEXT NOT NULL,source_id TEXT NOT NULL,session_id TEXT NOT NULL,
                quote TEXT NOT NULL,intent TEXT NOT NULL,observed_at TEXT NOT NULL,sequence INTEGER NOT NULL,
                UNIQUE(entry_id,source_hash,intent))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS observations_source ON observations(source_hash)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS memory_explicit_fences (
                scope TEXT NOT NULL,field TEXT NOT NULL,sequence INTEGER NOT NULL,
                PRIMARY KEY(scope,field))""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_fences (
                scope TEXT NOT NULL,field TEXT NOT NULL,sequence INTEGER NOT NULL,
                PRIMARY KEY(scope,field))""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_suppressions (
                scope TEXT NOT NULL,field TEXT NOT NULL,value TEXT NOT NULL,sequence INTEGER NOT NULL,
                PRIMARY KEY(scope,field,value))""")
            db.execute("""CREATE TABLE IF NOT EXISTS memory_learning_jobs (
                job_id TEXT PRIMARY KEY,state TEXT NOT NULL,attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL,summary TEXT NOT NULL)""")

    @contextmanager
    def transaction(self):
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
    def revision(db):
        return db.execute(
            "SELECT value FROM memory_metadata WHERE key='manual_revision'"
        ).fetchone()[0]

    @staticmethod
    def bump_revision(db):
        db.execute("UPDATE memory_metadata SET value=value+1 WHERE key='manual_revision'")

    @staticmethod
    def next_sequence(db):
        db.execute("UPDATE memory_metadata SET value=value+1 WHERE key='accepted_sequence'")
        return db.execute(
            "SELECT value FROM memory_metadata WHERE key='accepted_sequence'"
        ).fetchone()[0]

    @staticmethod
    def entries(db):
        return [
            MemoryEntry.model_validate_json(row[0])
            for row in db.execute("SELECT body FROM entries")
        ]

    @staticmethod
    def require_running(db, job_id):
        task = db.execute(
            "SELECT state FROM memory_learning_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if task is None or task[0] != "running":
            raise ContentError("memory_learning_cancelled", "记忆提交任务已停止，未写入候选", 409)

    @staticmethod
    def record_learning_write(db, job_id, effect):
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

    def committed_entries(self, db, source_id, at):
        row = db.execute(
            "SELECT entry_ids FROM sources WHERE id=?", (fingerprint(source_id),)
        ).fetchone()
        if row is None:
            return None
        ids = set(json.loads(row[0]))
        return {
            e.memory_id
            for e in self.entries(db)
            if e.memory_id in ids and e.state == "active" and not expired(e, at)
        }
