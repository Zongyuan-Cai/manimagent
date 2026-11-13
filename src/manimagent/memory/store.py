"""Deterministic typed preferences with ordered, evidence-backed transactions."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from manimagent.errors import ContentError

from .database import SCHEMA_VERSION, MemoryDatabase, expired, fingerprint
from .files import MemoryDocuments, publish_documents
from .models import MemoryEntry, MemoryEvidence, MemoryOptions, MemoryPolicy

POLICY_VERSION = "typed-user-memory-3"


def applicable_scopes(scope):
    return {
        "global",
        scope,
        *(["course." + scope.split(".")[1]] if scope.startswith("topic.") else []),
    }


class MemoryStore:
    def __init__(self, path: Path, policy: MemoryPolicy | None = None, clock=None):
        self.database = MemoryDatabase(path)
        self.path = self.database.path
        self.policy = policy or MemoryPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.documents = MemoryDocuments(self)

    def _now(self):
        at = self.clock()
        if at.tzinfo is None:
            raise ValueError("memory clock must return a timezone-aware datetime")
        return at

    @staticmethod
    def _save(db, entry):
        db.execute(
            "INSERT INTO entries VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (entry.memory_id, entry.model_dump_json()),
        )

    @staticmethod
    def _get(db, memory_id):
        row = db.execute("SELECT body FROM entries WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise ContentError("memory_not_found", "记忆不存在或已清理", 404)
        return MemoryEntry.model_validate_json(row[0])

    def revision(self):
        with self.database.transaction() as db:
            return self.database.revision(db)

    def _snapshot(self, db, session_id, scope, at):
        # Preserve all applicable candidates: the compiler handles exclusions and priority.
        entries = [
            e
            for e in self.database.entries(db)
            if e.state == "active" and not expired(e, at) and e.scope in applicable_scopes(scope)
        ]
        entries.sort(key=lambda e: (e.scope, e.field, e.value, e.memory_id))
        snapshot = {
            "policy_version": POLICY_VERSION,
            "schema_version": SCHEMA_VERSION,
            "revision": self.database.revision(db),
            "manual_revision": self.database.revision(db),
            "captured_at": at.isoformat(),
            "scope": scope,
            "session_id": session_id,
            "entries": [e.model_dump(exclude={"evidence"}) for e in entries],
        }
        if (
            len(entries) > self.policy.max_snapshot_entries
            or len(json.dumps(snapshot, ensure_ascii=False)) > self.policy.max_snapshot_chars
        ):
            raise ContentError(
                "memory_budget", "冻结记忆超过预算，请调整配置或清理条目；未静默裁剪", 422
            )
        return snapshot

    def snapshot(self, session_id, scope="global"):
        options = MemoryOptions(session_id=session_id, scope=scope)
        with self.database.transaction() as db:
            return self._snapshot(db, options.session_id, options.scope, self._now())

    @staticmethod
    def _input(session_id, source_text, scope):
        return fingerprint({"session_id": session_id, "scope": scope, "text": source_text})

    def _accept(self, db, source_id, session_id, source_text, scope, read):
        if (
            not isinstance(source_id, str)
            or not 1 <= len(source_id) <= 100
            or len(source_text) > 12000
        ):
            raise ContentError("invalid_memory", "记忆来源标识或输入长度无效", 422)
        signature = self._input(session_id, source_text, scope)
        row = db.execute(
            "SELECT sequence,signature,snapshot FROM acceptances WHERE source_hash=?",
            (fingerprint(source_id),),
        ).fetchone()
        if row:
            if row[1] != signature:
                raise ContentError("memory_source_conflict", "同一来源 ID 不能用于不同输入", 409)
            return {"sequence": row[0], "snapshot": json.loads(row[2]) if row[2] else None}
        count = db.execute(
            "SELECT COUNT(*) FROM (SELECT source_hash AS id FROM acceptances UNION SELECT id FROM sources)"
        ).fetchone()[0]
        if count >= self.policy.max_sources:
            raise ContentError("memory_capacity", "记忆来源索引已满，未丢弃防重放依据", 409)
        at = self._now()
        snapshot = self._snapshot(db, session_id, scope, at) if read else None
        sequence = self.database.next_sequence(db)
        db.execute(
            "INSERT INTO acceptances(source_hash,sequence,signature,snapshot,accepted_at) VALUES (?,?,?,?,?)",
            (
                fingerprint(source_id),
                sequence,
                signature,
                json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
                at.isoformat(),
            ),
        )
        return {"sequence": sequence, "snapshot": snapshot}

    def accept(self, source_id, session_id, source_text, scope="global", read=True):
        options = MemoryOptions(session_id=session_id, scope=scope, read=read)
        with self.database.transaction() as db:
            return self._accept(db, source_id, options.session_id, source_text, options.scope, read)

    def replay(self, *, session_id, source_id, source_text, scope="global"):
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT input_fingerprint,entry_ids FROM sources WHERE id=?",
                (fingerprint(source_id),),
            ).fetchone()
            if row is None:
                return None
            if row[0] != self._input(session_id, source_text, scope):
                raise ContentError("memory_source_conflict", "同一来源 ID 不能用于不同输入", 409)
            ids = json.loads(row[1])
            return [
                e
                for e in self.database.entries(db)
                if e.memory_id in ids and e.state == "active" and not expired(e, self._now())
            ]

    @staticmethod
    def _fence(db, scope, field, sequence):
        db.execute(
            "INSERT INTO memory_fences VALUES (?,?,?) ON CONFLICT(scope,field) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
            (scope, field, sequence),
        )

    def _supersede(self, db, chosen):
        for e in self.database.entries(db):
            if (
                e.memory_id != chosen.memory_id
                and e.scope == chosen.scope
                and e.field == chosen.field
                and e.state == "active"
                and e.layer == "deep"
            ):
                e.state = "superseded"
                self._save(db, e)

    def _recompute(self, db, entry):
        rows = db.execute(
            "SELECT source_id,session_id,quote,intent,observed_at,sequence FROM observations WHERE entry_id=? ORDER BY sequence,id",
            (entry.memory_id,),
        ).fetchall()
        if not rows:
            db.execute("DELETE FROM entries WHERE id=?", (entry.memory_id,))
            return None
        confirmations = [r for r in rows if r[3] in {"default", "correct", "restore"}]
        entry.confirmed_at = confirmations[-1][4] if confirmations else None
        latest = rows[-1]
        entry.session_id, entry.updated_at, entry.accepted_sequence = (
            latest[1],
            latest[4],
            latest[5],
        )
        entry.self_reported = entry.field.startswith("profile.")
        explicit = bool(confirmations)
        # A stable preference retains its historical support window; merely ageing
        # does not erase it. Source withdrawal recomputes that window from evidence.
        days = 365 if entry.self_reported else self.policy.short_term_days
        dated = [(datetime.fromisoformat(r[4]), r[1]) for r in rows]
        dated.sort()
        active_sessions = Counter()
        left, window_support = 0, 0
        for end, session in dated:
            active_sessions[session] += 1
            while dated[left][0] <= end - timedelta(days=days):
                prior_session = dated[left][1]
                active_sessions[prior_session] -= 1
                if not active_sessions[prior_session]:
                    del active_sessions[prior_session]
                left += 1
            window_support = max(window_support, len(active_sessions))
        promoted = not entry.self_reported and window_support >= self.policy.promotion_sessions
        current_start = datetime.fromisoformat(latest[4]) - timedelta(days=days)
        entry.independent_sessions = (
            len({r[1] for r in rows})
            if explicit or promoted
            else len({session for date, session in dated if date > current_start})
        )
        entry.layer = "deep" if explicit or promoted else "short_term"
        days = 365 if entry.self_reported else self.policy.short_term_days
        entry.expires_at = (
            (datetime.fromisoformat(latest[4]) + timedelta(days=days)).isoformat()
            if entry.self_reported or entry.layer == "short_term"
            else None
        )
        kinds = {
            "observe": "observation",
            "default": "confirmation",
            "correct": "correction",
            "restore": "restoration",
        }
        entry.evidence = [
            MemoryEvidence(source_id=r[0], session_id=r[1], quote=r[2], kind=kinds[r[3]], date=r[4])
            for r in rows[-self.policy.max_evidence_per_entry :]
        ]
        self._save(db, entry)
        return entry

    def _prune(self, db, at, protected=()):
        counts = {"expired": 0, "superseded": 0, "evicted": 0}
        for e in self.database.entries(db):
            reason = (
                "superseded" if e.state == "superseded" else "expired" if expired(e, at) else None
            )
            if reason:
                db.execute("DELETE FROM entries WHERE id=?", (e.memory_id,))
                counts[reason] += 1
        entries = self.database.entries(db)
        removable = sorted(
            (e for e in entries if e.layer == "short_term" and e.memory_id not in protected),
            key=lambda e: (e.accepted_sequence, e.memory_id),
        )
        overflow = len(entries) - self.policy.max_entries
        if overflow > len(removable):
            raise ContentError("memory_capacity", "记忆容量已满，未丢弃已确认默认", 409)
        for e in removable[: max(0, overflow)]:
            db.execute("DELETE FROM entries WHERE id=?", (e.memory_id,))
            counts["evicted"] += 1
        return counts

    def _apply(
        self,
        db,
        proposals,
        *,
        source_id,
        session_id,
        source_text,
        scope,
        sequence,
        learning_job_id=None,
    ):
        from manimagent.personalization import MemoryProposal

        proposals = [MemoryProposal.model_validate(p) for p in proposals]
        if type(sequence) is not int or sequence <= 0:
            raise ContentError("invalid_memory", "受理序号必须是正整数", 422)
        decisions = {}
        for proposal in proposals:
            if proposal.intent != "forget":
                key = (proposal.scope, proposal.field)
                if key in decisions and decisions[key] != proposal.value:
                    raise ContentError("invalid_memory", "同次候选对同范围字段存在冲突", 422)
                decisions[key] = proposal.value
        if len(proposals) > 20:
            raise ContentError("invalid_memory", "记忆候选超过20条上限", 422)
        for p in proposals:
            if not p.quote.strip() or p.quote not in source_text:
                raise ContentError("ungrounded_memory", "记忆证据必须逐字来自用户输入", 422)
            if p.scope not in applicable_scopes(scope):
                raise ContentError(
                    "invalid_memory_scope", "候选范围不属于用户选择的课程或主题", 422
                )
        signature = self._input(session_id, source_text, scope)
        source_hash = fingerprint(source_id)
        accepted = db.execute(
            "SELECT sequence,signature,accepted_at,withdrawn FROM acceptances WHERE source_hash=?",
            (source_hash,),
        ).fetchone()
        if not accepted or accepted[0] != sequence or accepted[1] != signature:
            raise ContentError("memory_source_conflict", "候选未绑定相同的已受理用户输入", 409)
        proposal_hash = fingerprint([p.model_dump() for p in proposals])
        if learning_job_id:
            if learning_job_id != source_id:
                raise ContentError("invalid_memory", "提交任务必须使用自身来源标识", 422)
            self.database.require_running(db, learning_job_id)
            queued = json.loads(
                db.execute(
                    "SELECT payload FROM memory_learning_jobs WHERE job_id=?", (learning_job_id,)
                ).fetchone()[0]
            )
            if queued["sequence"] != sequence or fingerprint(queued["proposals"]) != proposal_hash:
                raise ContentError("memory_source_conflict", "提交候选与持久任务不一致", 409)
        prior = db.execute(
            "SELECT fingerprint,input_fingerprint,entry_ids FROM sources WHERE id=?", (source_hash,)
        ).fetchone()
        if prior:
            if prior[:2] != (proposal_hash, signature):
                raise ContentError("memory_source_conflict", "同一来源不能改写已经提交的候选", 409)
            ids = set(json.loads(prior[2]))
            if learning_job_id:
                self.database.record_learning_write(
                    db,
                    learning_job_id,
                    {
                        "created_ids": [],
                        "reinforced_ids": [],
                        "promoted_ids": [],
                        "skipped_proposals": [],
                        "replayed": True,
                    },
                )
            return [
                e
                for e in self.database.entries(db)
                if e.memory_id in ids and e.state == "active" and not expired(e, self._now())
            ]
        at = self._now()
        before = {e.memory_id: e.layer for e in self.database.entries(db)}
        changed, skipped = set(), []
        if accepted[3]:
            skipped.append({"reason": "source_withdrawn"})
            proposals = []
        for p in proposals:
            candidate_days = 365 if p.field.startswith("profile.") else self.policy.short_term_days
            if (p.intent == "observe" or p.field.startswith("profile.")) and datetime.fromisoformat(
                accepted[2]
            ) + timedelta(days=candidate_days) <= at:
                skipped.append({"field": p.field, "reason": "expired_before_commit"})
                continue
            key = (p.scope, p.field)
            explicit_fence = db.execute(
                "SELECT sequence FROM memory_explicit_fences WHERE scope=? AND field=?", key
            ).fetchone()
            if explicit_fence and sequence < explicit_fence[0]:
                skipped.append({"field": p.field, "reason": "newer_explicit_decision"})
                continue
            barrier = db.execute(
                "SELECT sequence FROM memory_fences WHERE scope=? AND field=?", key
            ).fetchone()
            same_stable_value = p.intent == "observe" and any(
                e.state == "active"
                and e.layer == "deep"
                and (e.scope, e.field, e.value) == (*key, p.value)
                for e in self.database.entries(db)
            )
            if barrier and sequence < barrier[0] and not same_stable_value:
                skipped.append({"field": p.field, "reason": "newer_decision"})
                continue
            suppressed = db.execute(
                "SELECT sequence FROM memory_suppressions WHERE scope=? AND field=? AND value=?",
                (*key, p.value),
            ).fetchone()
            if (
                suppressed
                and p.intent != "forget"
                and (p.intent != "restore" or sequence <= suppressed[0])
            ):
                skipped.append({"field": p.field, "reason": "suppressed"})
                continue
            if p.intent != "observe":
                db.execute(
                    "INSERT INTO memory_explicit_fences VALUES (?,?,?) ON CONFLICT(scope,field) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                    (*key, sequence),
                )
            if p.intent == "forget":
                db.execute(
                    "INSERT INTO memory_suppressions VALUES (?,?,?,?) ON CONFLICT(scope,field,value) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                    (*key, p.value, sequence),
                )
                for e in self.database.entries(db):
                    if (e.scope, e.field, e.value) == (*key, p.value):
                        db.execute("DELETE FROM entries WHERE id=?", (e.memory_id,))
                self._fence(db, *key, sequence)
                continue
            if p.intent == "restore":
                db.execute(
                    "DELETE FROM memory_suppressions WHERE scope=? AND field=? AND value=?",
                    (*key, p.value),
                )
            entry = next(
                (
                    e
                    for e in self.database.entries(db)
                    if e.state == "active"
                    and not expired(e, at)
                    and (e.scope, e.field, e.value) == (*key, p.value)
                ),
                None,
            )
            if entry is None:
                entry = MemoryEntry(
                    memory_id="memory_" + uuid4().hex,
                    field=p.field,
                    value=p.value,
                    scope=p.scope,
                    session_id=session_id,
                    created_at=accepted[2],
                    updated_at=accepted[2],
                )
                self._save(db, entry)
            db.execute(
                "INSERT OR IGNORE INTO observations(entry_id,source_hash,source_id,session_id,quote,intent,observed_at,sequence) VALUES (?,?,?,?,?,?,?,?)",
                (
                    entry.memory_id,
                    source_hash,
                    source_id,
                    session_id,
                    p.quote,
                    p.intent,
                    accepted[2],
                    sequence,
                ),
            )
            entry = self._recompute(db, entry)
            if entry.layer == "deep":
                self._supersede(db, entry)
                self._fence(db, *key, entry.accepted_sequence)
            changed.add(entry.memory_id)
        self._prune(db, at, protected=changed)
        entries = [
            e
            for e in self.database.entries(db)
            if e.memory_id in changed and e.state == "active" and not expired(e, at)
        ]
        ids = [e.memory_id for e in entries]
        db.execute(
            "INSERT INTO sources VALUES (?,?,?,?)",
            (source_hash, proposal_hash, json.dumps(ids), signature),
        )
        self.database.bump_revision(db)
        if learning_job_id:
            self.database.record_learning_write(
                db,
                learning_job_id,
                {
                    "created_ids": [i for i in ids if i not in before],
                    "reinforced_ids": [i for i in ids if i in before],
                    "promoted_ids": [
                        e.memory_id
                        for e in entries
                        if e.layer == "deep" and before.get(e.memory_id) != "deep"
                    ],
                    "skipped_proposals": skipped,
                },
            )
        return entries

    @publish_documents
    def observe_proposals(
        self,
        proposals,
        *,
        source_id,
        session_id,
        source_text,
        scope="global",
        sequence,
        learning_job_id=None,
    ):
        options = MemoryOptions(session_id=session_id, scope=scope)
        with self.database.transaction() as db:
            return self._apply(
                db,
                proposals,
                source_id=source_id,
                session_id=options.session_id,
                source_text=source_text,
                scope=options.scope,
                sequence=sequence,
                learning_job_id=learning_job_id,
            )

    def list_entries(self):
        with self.database.transaction() as db:
            return sorted(
                (
                    e
                    for e in self.database.entries(db)
                    if e.state == "active" and not expired(e, self._now())
                ),
                key=lambda e: (e.accepted_sequence, e.memory_id),
                reverse=True,
            )

    def _manage(self, db, entry, value, intent):
        from manimagent.personalization import MemoryProposal

        if entry.state != "active" or expired(entry, self._now()):
            raise ContentError("invalid_memory", "过期或已替代的记忆不能修改", 409)
        source_id = "management_" + uuid4().hex
        try:
            proposal = MemoryProposal(
                field=entry.field, value=value, scope=entry.scope, intent=intent, quote=value
            )
        except ValidationError as exc:
            raise ContentError("invalid_memory", "该字段不支持此规范取值", 422) from exc
        accepted = self._accept(db, source_id, entry.session_id, value, entry.scope, False)
        result = self._apply(
            db,
            [proposal],
            source_id=source_id,
            session_id=entry.session_id,
            source_text=value,
            scope=entry.scope,
            sequence=accepted["sequence"],
        )
        if not result and intent != "forget":
            raise ContentError(
                "memory_suppressed", "此取值已被遗忘，请明确重新记住后再设为默认", 409
            )
        return result[0] if result else None

    @publish_documents
    def confirm(self, memory_id):
        with self.database.transaction() as db:
            entry = self._get(db, memory_id)
            return self._manage(db, entry, entry.value, "default")

    @publish_documents
    def correct(self, memory_id, value, *, confirm_long_term=False):
        with self.database.transaction() as db:
            old = self._get(db, memory_id)
            result = self._manage(db, old, value, "correct")
            if result and result.memory_id != old.memory_id:
                db.execute("DELETE FROM entries WHERE id=?", (old.memory_id,))
            return result

    @publish_documents
    def delete(self, memory_id):
        with self.database.transaction() as db:
            entry = self._get(db, memory_id)
            # Forgetting must remain available when the source ledger is full.
            # It needs ordered revocation, not a new supporting observation.
            sequence = self.database.next_sequence(db)
            key = (entry.scope, entry.field)
            db.execute(
                "INSERT INTO memory_explicit_fences VALUES (?,?,?) ON CONFLICT(scope,field) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                (*key, sequence),
            )
            db.execute(
                "INSERT INTO memory_suppressions VALUES (?,?,?,?) ON CONFLICT(scope,field,value) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                (*key, entry.value, sequence),
            )
            for existing in self.database.entries(db):
                if (existing.scope, existing.field, existing.value) == (*key, entry.value):
                    db.execute("DELETE FROM entries WHERE id=?", (existing.memory_id,))
            self._fence(db, *key, sequence)
            self.database.bump_revision(db)

    @publish_documents
    def withdraw_source(self, source_id):
        with self.database.transaction() as db:
            source_hash = fingerprint(source_id)
            accepted = db.execute(
                "SELECT 1 FROM acceptances WHERE source_hash=?", (source_hash,)
            ).fetchone()
            if not accepted:
                raise ContentError("memory_source_not_found", "记忆来源不存在", 404)
            affected = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT entry_id FROM observations WHERE source_hash=?", (source_hash,)
                )
            ]
            db.execute("UPDATE acceptances SET withdrawn=1 WHERE source_hash=?", (source_hash,))
            db.execute("DELETE FROM observations WHERE source_hash=?", (source_hash,))
            for identifier in affected:
                self._recompute(db, self._get(db, identifier))
            self.database.bump_revision(db)
            return {"withdrawn": True, "affected_ids": affected}

    def was_withdrawn(self, source_id):
        """Durable source cancellation survives bounded learning-job history."""
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT withdrawn FROM acceptances WHERE source_hash=?",
                (fingerprint(source_id),),
            ).fetchone()
            return bool(row and row[0])

    @publish_documents
    def cleanup(self):
        with self.database.transaction() as db:
            counts = self._prune(db, self._now())
            if any(counts.values()):
                self.database.bump_revision(db)
            return {**counts, "remaining": db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]}

    def sync_documents(self):
        return self.documents.sync()

    def read_document(self, memory_id):
        return self.documents.read(memory_id)

    def import_document(self, memory_id, content=None):
        raise ContentError(
            "memory_document_read_only", "记忆 Markdown 仅用于导出，请通过结构化管理修改默认", 409
        )
