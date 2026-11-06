"""Transactional local user memory with bounded evidence and immutable source keys."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from manimagent.errors import ContentError

from .models import MemoryCandidate, MemoryEntry, MemoryEvidence, MemoryOptions, MemoryPolicy

POLICY_VERSION = "user-memory-1"


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class MemoryStore:
    def __init__(
        self,
        path: Path,
        policy: MemoryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.path = Path(path)
        self.policy = policy or MemoryPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
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

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
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

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("memory clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _entries(db) -> list[MemoryEntry]:
        return [
            MemoryEntry.model_validate_json(row[0])
            for row in db.execute("SELECT body FROM entries")
        ]

    @staticmethod
    def _save(db, entry: MemoryEntry):
        db.execute(
            "INSERT INTO entries(id,body) VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (entry.memory_id, entry.model_dump_json()),
        )

    @staticmethod
    def _get(db, memory_id) -> MemoryEntry:
        row = db.execute("SELECT body FROM entries WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise ContentError("memory_not_found", "记忆不存在或已清理", 404)
        return MemoryEntry.model_validate_json(row[0])

    @staticmethod
    def _expired(entry, at):
        return entry.expires_at is not None and datetime.fromisoformat(entry.expires_at) <= at

    def _prune(self, db, at, reserve=0, protected=()) -> dict:
        counts = {"expired": 0, "superseded": 0, "evicted": 0}
        for entry in self._entries(db):
            reason = (
                "superseded"
                if entry.state == "superseded"
                else "expired"
                if self._expired(entry, at)
                else None
            )
            if reason and entry.memory_id not in protected:
                db.execute("DELETE FROM entries WHERE id=?", (entry.memory_id,))
                counts[reason] += 1
        entries = self._entries(db)
        excess = max(0, len(entries) + reserve - self.policy.max_entries)
        candidates = sorted(
            (e for e in entries if e.memory_id not in protected and e.layer == "short_term"),
            key=lambda e: (not e.temporary, e.independent_sessions, e.updated_at, e.memory_id),
        )
        if len(candidates) < excess:
            raise ContentError("memory_capacity", "记忆容量已满，请删除不再需要的深层记忆", 409)
        for entry in candidates[:excess]:
            db.execute("DELETE FROM entries WHERE id=?", (entry.memory_id,))
            counts["evicted"] += 1
        return counts

    def _supersede(self, db, entry, at):
        for other in self._entries(db):
            if (
                other.memory_id != entry.memory_id
                and other.state == "active"
                and other.scope == entry.scope
                and normalized(other.field) == normalized(entry.field)
                and not other.temporary
            ):
                other.state = "superseded"
                other.updated_at = at.isoformat()
                self._save(db, other)

    def _append_evidence(self, entry, evidence):
        entry.evidence = (entry.evidence + [evidence])[-self.policy.max_evidence_per_entry :]

    def replay(
        self, *, session_id: str, source_id: str, source_text: str, scope: str = "global"
    ) -> list[MemoryEntry] | None:
        """Return prior results before another extraction call; [] includes deletion."""
        options = MemoryOptions(session_id=session_id, scope=scope)
        signature = fingerprint(
            {
                "session_id": options.session_id,
                "scope": options.scope,
                "text": source_text,
            }
        )
        with self._transaction() as db:
            previous = db.execute(
                "SELECT input_fingerprint,entry_ids FROM sources WHERE id=?",
                (fingerprint(source_id),),
            ).fetchone()
            if previous is None:
                return None
            if previous[0] != signature:
                raise ContentError(
                    "memory_source_conflict", "同一来源 ID 不能用于不同记忆输入", 409
                )
            ids = set(json.loads(previous[1]))
            return [entry for entry in self._entries(db) if entry.memory_id in ids]

    def observe(
        self,
        candidates: list[MemoryCandidate],
        *,
        session_id: str,
        source_id: str,
        source_text: str,
        scope: str = "global",
    ) -> list[MemoryEntry]:
        options = MemoryOptions(session_id=session_id, scope=scope)
        session_id, scope = options.session_id, options.scope
        if not source_id.strip() or len(source_id) > 200:
            raise ContentError("invalid_memory", "记忆来源 ID 无效")
        if len(candidates) > 12:
            raise ContentError("invalid_memory", "每次最多提取 12 条记忆")
        candidates = [MemoryCandidate.model_validate(c) for c in candidates]
        for candidate in candidates:
            if candidate.evidence not in source_text:
                raise ContentError("ungrounded_memory", "记忆证据必须逐字来自用户输入")
        key = fingerprint(source_id)
        input_data = {"session_id": session_id, "scope": scope, "text": source_text}
        signature = fingerprint(
            {
                **input_data,
                "candidates": [c.model_dump() for c in candidates],
            }
        )
        at = self._now()
        with self._transaction() as db:
            previous = db.execute(
                "SELECT fingerprint,entry_ids FROM sources WHERE id=?", (key,)
            ).fetchone()
            if previous:
                if previous[0] != signature:
                    raise ContentError(
                        "memory_source_conflict", "同一来源 ID 不能用于不同记忆输入", 409
                    )
                ids = set(json.loads(previous[1]))
                return [e for e in self._entries(db) if e.memory_id in ids]
            source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            if source_count >= self.policy.max_sources:
                raise ContentError("memory_capacity", "记忆来源索引已满，暂停新的自动记忆写入", 409)
            self._prune(db, at)
            changed = []
            for index, candidate in enumerate(candidates):
                field, value = normalized(candidate.field), normalized(candidate.value)
                entry = next(
                    (
                        e
                        for e in self._entries(db)
                        if (
                            e.state == "active"
                            and e.scope == scope
                            and normalized(e.field) == field
                            and normalized(e.value) == value
                            and e.temporary == candidate.temporary
                            and (not candidate.temporary or e.session_id == session_id)
                        )
                    ),
                    None,
                )
                if entry is None:
                    # Evict after processing the batch: a new confirming value may
                    # supersede a deep entry even when the store is currently full.
                    entry = MemoryEntry(
                        memory_id="memory_" + uuid4().hex,
                        field=field,
                        value=value,
                        scope=scope,
                        temporary=candidate.temporary,
                        session_id=session_id,
                        created_at=at.isoformat(),
                        updated_at=at.isoformat(),
                    )
                entry.updated_at = at.isoformat()
                entry.session_id = session_id
                self._save(db, entry)
                db.execute(
                    "INSERT INTO sessions(entry_id,session_id,last_seen,last_order) VALUES (?,?,?,?) "
                    "ON CONFLICT(entry_id,session_id) DO UPDATE SET "
                    "last_seen=excluded.last_seen,last_order=excluded.last_order",
                    (entry.memory_id, session_id, at.isoformat(), source_count * 100 + index + 1),
                )
                # Sources are bounded, so independent-session metadata is bounded too.
                entry.independent_sessions = db.execute(
                    "SELECT COUNT(*) FROM sessions WHERE entry_id=?", (entry.memory_id,)
                ).fetchone()[0]
                self._append_evidence(
                    entry,
                    MemoryEvidence(
                        source_id=source_id,
                        session_id=session_id,
                        quote=candidate.evidence,
                        date=at.isoformat(),
                    ),
                )
                if (
                    entry.layer == "short_term"
                    and not entry.temporary
                    and entry.independent_sessions >= self.policy.promotion_sessions
                ):
                    entry.layer = "deep"
                    self._supersede(db, entry, at)
                entry.expires_at = (
                    (at + timedelta(days=self.policy.short_term_days)).isoformat()
                    if entry.layer == "short_term"
                    else None
                )
                self._save(db, entry)
                if entry.memory_id not in changed:
                    changed.append(entry.memory_id)
            self._prune(db, at, protected=changed)
            db.execute(
                "INSERT INTO sources(id,fingerprint,entry_ids,input_fingerprint) VALUES (?,?,?,?)",
                (key, signature, json.dumps(changed), fingerprint(input_data)),
            )
            return [self._get(db, identifier) for identifier in changed]

    def list_entries(self) -> list[MemoryEntry]:
        with self._transaction() as db:
            return sorted(
                self._entries(db), key=lambda e: (e.updated_at, e.memory_id), reverse=True
            )

    def confirm(self, memory_id: str) -> MemoryEntry:
        at = self._now()
        with self._transaction() as db:
            entry = self._get(db, memory_id)
            if entry.temporary or entry.state != "active" or self._expired(entry, at):
                raise ContentError(
                    "invalid_memory", "临时、过期或已替代的记忆不能直接确认为长期偏好"
                )
            entry.layer, entry.expires_at = "deep", None
            entry.updated_at = entry.confirmed_at = at.isoformat()
            self._append_evidence(
                entry,
                MemoryEvidence(
                    source_id="confirmation_" + uuid4().hex,
                    session_id=entry.session_id,
                    quote=entry.value,
                    date=at.isoformat(),
                    kind="confirmation",
                ),
            )
            self._supersede(db, entry, at)
            self._save(db, entry)
            self._prune(db, at, protected=[entry.memory_id])
            return entry

    def correct(self, memory_id: str, value: str) -> MemoryEntry:
        at = self._now()
        with self._transaction() as db:
            old = self._get(db, memory_id)
            candidate = MemoryCandidate(field=old.field, value=value, evidence=value)
            entry = MemoryEntry(
                memory_id="memory_" + uuid4().hex,
                field=old.field,
                value=normalized(candidate.value),
                scope=old.scope,
                layer="deep",
                session_id=old.session_id,
                independent_sessions=0,
                created_at=at.isoformat(),
                updated_at=at.isoformat(),
                confirmed_at=at.isoformat(),
                evidence=[
                    MemoryEvidence(
                        source_id="correction_" + uuid4().hex,
                        session_id=old.session_id,
                        quote=candidate.value,
                        date=at.isoformat(),
                        kind="correction",
                    )
                ],
            )
            old.state, old.updated_at = "superseded", at.isoformat()
            self._save(db, old)
            self._supersede(db, entry, at)
            self._save(db, entry)
            self._prune(db, at, protected=[entry.memory_id])
            return entry

    def delete(self, memory_id: str) -> None:
        with self._transaction() as db:
            self._get(db, memory_id)
            db.execute("DELETE FROM entries WHERE id=?", (memory_id,))

    def cleanup(self) -> dict:
        with self._transaction() as db:
            counts = self._prune(db, self._now())
            return {**counts, "remaining": db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]}

    def snapshot(self, session_id: str, scope: str = "global") -> dict:
        options = MemoryOptions(session_id=session_id, scope=scope)
        session_id, scope = options.session_id, options.scope
        at = self._now()
        with self._transaction() as db:
            entries = [
                e
                for e in self._entries(db)
                if (
                    e.state == "active"
                    and not self._expired(e, at)
                    and e.scope in {"global", scope}
                )
            ]
            seen = {
                row[0]: (row[1], row[2])
                for row in db.execute(
                    "SELECT entry_id,last_seen,last_order FROM sessions WHERE session_id=?",
                    (session_id,),
                )
            }
            seen = {
                identifier: details
                for identifier, details in seen.items()
                if datetime.fromisoformat(details[0])
                > at - timedelta(days=self.policy.short_term_days)
            }
            entries = [e for e in entries if e.layer == "deep" or e.memory_id in seen]
            # A current-session request overrides the stored default only in that
            # session; a topic-specific memory takes precedence over a global one.
            entries.sort(
                key=lambda e: (
                    e.scope == scope,
                    e.memory_id in seen,
                    seen.get(e.memory_id, ("", 0))[1],
                    e.updated_at,
                    e.layer == "deep",
                    e.memory_id,
                ),
                reverse=True,
            )
            selected, fields = [], set()
            snapshot = {
                "policy_version": POLICY_VERSION,
                "captured_at": at.isoformat(),
                "scope": scope,
                "session_id": session_id,
                "entries": selected,
            }
            if len(json.dumps(snapshot, ensure_ascii=False)) > self.policy.max_snapshot_chars:
                raise ContentError("memory_budget", "记忆上下文预算不足以容纳会话信息")
            for entry in entries:
                if entry.field in fields:
                    continue
                # Register the winning value before budget checks: truncation must
                # never revive an older, contradictory value for the same field.
                fields.add(entry.field)
                item = {
                    key: getattr(entry, key)
                    for key in (
                        "memory_id",
                        "field",
                        "value",
                        "scope",
                        "layer",
                        "temporary",
                        "updated_at",
                    )
                }
                selected.append(item)
                if len(json.dumps(snapshot, ensure_ascii=False)) > self.policy.max_snapshot_chars:
                    selected.pop()
                if len(selected) == self.policy.max_snapshot_entries:
                    break
            return snapshot
