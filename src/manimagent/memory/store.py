"""Transactional local user memory with bounded evidence and immutable source keys."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from manimagent.errors import ContentError

from .database import MemoryDatabase, expired, fingerprint
from .files import MemoryDocuments, parse_edit, publish_documents
from .models import MemoryCandidate, MemoryEntry, MemoryEvidence, MemoryOptions, MemoryPolicy

POLICY_VERSION = "user-memory-2"


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class MemoryStore:
    def __init__(
        self,
        path: Path,
        policy: MemoryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = MemoryDatabase(path)
        self.path = self.database.path
        self.policy = policy or MemoryPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.documents = MemoryDocuments(self)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("memory clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def revision(self) -> int:
        with self.database.transaction() as db:
            return self.database.revision(db)

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

    def _prune(self, db, at, protected=()) -> dict:
        counts = {"expired": 0, "superseded": 0, "evicted": 0}
        for entry in self.database.entries(db):
            reason = (
                "superseded"
                if entry.state == "superseded"
                else "expired"
                if expired(entry, at)
                else None
            )
            if reason and entry.memory_id not in protected:
                db.execute("DELETE FROM entries WHERE id=?", (entry.memory_id,))
                counts[reason] += 1
        entries = self.database.entries(db)
        excess = max(0, len(entries) - self.policy.max_entries)
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
        for other in self.database.entries(db):
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
        with self.database.transaction() as db:
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
            return [entry for entry in self.database.entries(db) if entry.memory_id in ids]

    @publish_documents
    def observe(
        self,
        candidates: list[MemoryCandidate],
        *,
        session_id: str,
        source_id: str,
        source_text: str,
        scope: str = "global",
        expected_revision: int | None = None,
        learning_job_id: str | None = None,
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
        with self.database.transaction() as db:
            previous = db.execute(
                "SELECT fingerprint,entry_ids FROM sources WHERE id=?", (key,)
            ).fetchone()
            if previous:
                if previous[0] != signature:
                    raise ContentError(
                        "memory_source_conflict", "同一来源 ID 不能用于不同记忆输入", 409
                    )
                ids = set(json.loads(previous[1]))
                return [e for e in self.database.entries(db) if e.memory_id in ids]
            if expected_revision is not None and expected_revision != self.database.revision(db):
                raise ContentError(
                    "memory_changed", "记忆已被你修改，旧的后台提取结果已停止写入", 409
                )
            if learning_job_id is not None:
                if learning_job_id != source_id:
                    raise ContentError("invalid_memory", "后台记忆来源必须与任务一致")
                self.database.require_running(db, learning_job_id)
            source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            if source_count >= self.policy.max_sources:
                raise ContentError("memory_capacity", "记忆来源索引已满，暂停新的自动记忆写入", 409)
            self._prune(db, at)
            before = {entry.memory_id: entry.layer for entry in self.database.entries(db)}
            changed = []
            for candidate in candidates:
                field, value = normalized(candidate.field), normalized(candidate.value)
                entry = next(
                    (
                        e
                        for e in self.database.entries(db)
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
                order = db.execute(
                    "SELECT COALESCE(MAX(last_order), 0) + 1 FROM sessions"
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO sessions(entry_id,session_id,last_seen,last_order) VALUES (?,?,?,?) "
                    "ON CONFLICT(entry_id,session_id) DO UPDATE SET "
                    "last_seen=excluded.last_seen,last_order=excluded.last_order",
                    (entry.memory_id, session_id, at.isoformat(), order),
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
            result = [self._get(db, identifier) for identifier in changed]
            if learning_job_id is not None:
                # Commit the exact effect alongside memory and the source ledger.
                # Recovery can finish this task without another paid extraction.
                effect = {
                    "entry_ids": changed,
                    "created_ids": [
                        entry.memory_id for entry in result if entry.memory_id not in before
                    ],
                    "reinforced_ids": [
                        entry.memory_id for entry in result if entry.memory_id in before
                    ],
                    "promoted_ids": [
                        entry.memory_id
                        for entry in result
                        if before.get(entry.memory_id) == "short_term" and entry.layer == "deep"
                    ],
                }
                self.database.record_learning_write(db, learning_job_id, effect)
            return result

    def list_entries(self) -> list[MemoryEntry]:
        with self.database.transaction() as db:
            return sorted(
                self.database.entries(db), key=lambda e: (e.updated_at, e.memory_id), reverse=True
            )

    @publish_documents
    def confirm(self, memory_id: str) -> MemoryEntry:
        at = self._now()
        with self.database.transaction() as db:
            entry = self._get(db, memory_id)
            if entry.temporary or entry.state != "active" or expired(entry, at):
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
            self.database.bump_revision(db)
            return entry

    @publish_documents
    def correct(
        self,
        memory_id: str,
        value: str,
        *,
        confirm_long_term: bool = False,
        document: str | None = None,
    ) -> MemoryEntry:
        at = self._now()
        with self.database.transaction() as db:
            old = self._get(db, memory_id)
            if old.state != "active" or expired(old, at):
                raise ContentError("invalid_memory", "过期或已替代的记忆不能纠正，请添加新的表达")
            if document is not None:
                if confirm_long_term:
                    raise ContentError(
                        "memory_document_invalid", "文件导入不能同时改变记忆层级", 422
                    )
                value = parse_edit(document, old)
                if normalized(value) == old.value:
                    return old
            candidate = MemoryCandidate(field=old.field, value=value, evidence=value)
            entry = MemoryEntry(
                memory_id="memory_" + uuid4().hex,
                field=old.field,
                value=normalized(candidate.value),
                scope=old.scope,
                layer="deep" if confirm_long_term else old.layer,
                temporary=False if confirm_long_term else old.temporary,
                session_id=old.session_id,
                independent_sessions=1,
                created_at=at.isoformat(),
                updated_at=at.isoformat(),
                expires_at=None if confirm_long_term else old.expires_at,
                confirmed_at=at.isoformat() if confirm_long_term else old.confirmed_at,
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
            if entry.layer == "deep":
                self._supersede(db, entry, at)
            self._save(db, entry)
            # A correction is one new explicit statement, not evidence that all
            # historical sessions endorsed its new value. Preserve its original
            # session's override without copying the old support count.
            order = db.execute("SELECT COALESCE(MAX(last_order), 0) + 1 FROM sessions").fetchone()[
                0
            ]
            db.execute(
                "INSERT INTO sessions(entry_id,session_id,last_seen,last_order) VALUES (?,?,?,?)",
                (entry.memory_id, entry.session_id, at.isoformat(), order),
            )
            self._prune(db, at, protected=[entry.memory_id])
            self.database.bump_revision(db)
            return entry

    @publish_documents
    def delete(self, memory_id: str) -> None:
        with self.database.transaction() as db:
            self._get(db, memory_id)
            db.execute("DELETE FROM entries WHERE id=?", (memory_id,))
            self.database.bump_revision(db)

    @publish_documents
    def cleanup(self) -> dict:
        with self.database.transaction() as db:
            counts = self._prune(db, self._now())
            if counts["evicted"]:
                self.database.bump_revision(db)
            return {**counts, "remaining": db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]}

    def sync_documents(self) -> dict:
        return self.documents.sync()

    def read_document(self, memory_id: str) -> str:
        return self.documents.read(memory_id)

    def import_document(self, memory_id: str, content: str | None = None) -> MemoryEntry:
        self.documents.path(memory_id)
        content = self.read_document(memory_id) if content is None else content
        try:
            result = self.correct(memory_id, "", document=content)
        except ValidationError as exc:
            raise ContentError(
                "memory_document_invalid", "记忆内容必须为 1 到 300 个有效字符", 422
            ) from exc
        except ContentError as exc:
            if exc.error.code == "memory_not_found":
                raise ContentError(
                    "memory_document_conflict", "原记忆已删除或清理，文件不能重新创建该记忆", 409
                ) from exc
            raise
        self.documents.accepted(memory_id, content)
        self.sync_documents()
        return result

    def snapshot(self, session_id: str, scope: str = "global") -> dict:
        options = MemoryOptions(session_id=session_id, scope=scope)
        session_id, scope = options.session_id, options.scope
        at = self._now()
        with self.database.transaction() as db:
            entries = [
                e
                for e in self.database.entries(db)
                if (e.state == "active" and not expired(e, at) and e.scope in {"global", scope})
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
            entries = [e for e in entries if not e.temporary or e.session_id == session_id]
            # Recent non-temporary facts remain available across sessions. Explicit
            # statements in this session win first (latest statement wins ties),
            # then applicable scope, then stable defaults over unconfirmed changes.
            entries.sort(
                key=lambda e: (
                    e.memory_id in seen,
                    seen.get(e.memory_id, ("", 0))[1],
                    e.scope == scope,
                    e.layer == "deep",
                    e.updated_at,
                    e.memory_id,
                ),
                reverse=True,
            )
            # Resolve each field before applying capacity limits. The losing
            # value must not reappear when its winning value is too large.
            winners = {}
            for entry in entries:
                winners.setdefault(entry.field, entry)
            # Across unrelated fields, evidence recency/order is bookkeeping,
            # not a reason to evict a semantically unchanged fact from context.
            # Current-session and scoped requirements retain their precedence;
            # temporary exceptions are important for this session only.
            entries = sorted(
                winners.values(),
                key=lambda e: (
                    e.memory_id not in seen,
                    e.scope != scope,
                    not e.temporary,
                    e.layer != "deep",
                    e.scope,
                    e.field,
                ),
            )
            selected = []
            snapshot = {
                "policy_version": POLICY_VERSION,
                "manual_revision": self.database.revision(db),
                "captured_at": at.isoformat(),
                "scope": scope,
                "session_id": session_id,
                "entries": selected,
            }
            if len(json.dumps(snapshot, ensure_ascii=False)) > self.policy.max_snapshot_chars:
                raise ContentError("memory_budget", "记忆上下文预算不足以容纳会话信息")
            for entry in entries:
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
