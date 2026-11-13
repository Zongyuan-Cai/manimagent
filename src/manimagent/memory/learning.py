"""Durable queue of already interpreted candidates; no model extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from manimagent.errors import ContentError

from .database import MemoryDatabase, fingerprint
from .models import MemoryOptions

STATES = ("queued", "running", "completed", "failed", "skipped", "cancelled")
ACTIVE = {"queued", "running"}
TERMINAL = set(STATES) - ACTIVE
MAX_PENDING, MAX_HISTORY, MAX_ATTEMPTS = 100, 200, 3


def safe_error(error):
    if error is None:
        return None
    messages = {
        "interrupted": "记忆提交被服务重启中断，可重试",
        "memory_learning_cancelled": "已取消记忆提交",
        "memory_capacity": "记忆存储容量已满",
        "ungrounded_memory": "候选缺少用户原句依据",
        "memory_source_conflict": "候选来源绑定冲突",
        "memory_internal_error": "记忆提交失败，请检查服务日志",
    }
    code = error.get("code", "memory_internal_error")
    if code not in messages:
        code = "memory_internal_error"
    return {
        "code": code,
        "message": messages[code],
        "retryable": bool(error.get("retryable", False)),
    }


class LearningStore:
    def __init__(self, path: Path):
        self.database = MemoryDatabase(path)
        self.path = self.database.path

    @staticmethod
    def _row(db, job_id):
        row = db.execute(
            "SELECT job_id,state,attempts,created_at,updated_at,payload,summary FROM memory_learning_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if not row:
            raise ContentError("memory_learning_not_found", "记忆提交任务不存在或已清理", 404)
        return dict(
            zip(
                ("job_id", "state", "attempts", "created_at", "updated_at", "payload", "summary"),
                (*row[:5], json.loads(row[5]), json.loads(row[6])),
                strict=True,
            )
        )

    @staticmethod
    def _save(db, task):
        db.execute(
            "UPDATE memory_learning_jobs SET state=?,attempts=?,updated_at=?,payload=?,summary=? WHERE job_id=?",
            (
                task["state"],
                task["attempts"],
                task["updated_at"],
                json.dumps(task["payload"], ensure_ascii=False),
                json.dumps(task["summary"], ensure_ascii=False),
                task["job_id"],
            ),
        )

    @staticmethod
    def _summary(task):
        return {
            "entry_ids": [],
            "promoted_ids": [],
            "counts": {"created": 0, "reinforced": 0, "promoted": 0},
            "counts_known": False,
            "usage": {},
            "usage_unknown": False,
            "attempt_history": [],
            "error": None,
            **task["summary"],
            **{k: task[k] for k in ("job_id", "state", "attempts", "created_at", "updated_at")},
            "max_attempts": MAX_ATTEMPTS,
            "can_retry": task["state"] == "failed"
            and task["attempts"] < MAX_ATTEMPTS
            and "proposals" in task["payload"],
            "can_cancel": task["state"] in ACTIVE,
        }

    @staticmethod
    def _capacity(db):
        if (
            db.execute(
                "SELECT COUNT(*) FROM memory_learning_jobs WHERE state IN ('queued','running')"
            ).fetchone()[0]
            >= MAX_PENDING
        ):
            raise ContentError("memory_learning_capacity", "记忆提交队列已满", 429)

    @staticmethod
    def _prune_history(db):
        rows = db.execute(
            "SELECT job_id FROM memory_learning_jobs WHERE state IN ('completed','failed','skipped','cancelled') ORDER BY updated_at DESC,rowid DESC"
        ).fetchall()
        db.executemany("DELETE FROM memory_learning_jobs WHERE job_id=?", rows[MAX_HISTORY:])
        return max(0, len(rows) - MAX_HISTORY)

    def enqueue(self, job_id, proposals, options, sequence, source_text):
        from manimagent.personalization import MemoryProposal

        options = MemoryOptions.model_validate(options)
        if not options.learn:
            raise ContentError("memory_learning_disabled", "本次未开启记忆学习", 409)
        items = [MemoryProposal.model_validate(p).model_dump() for p in proposals]
        if (
            type(sequence) is not int
            or sequence <= 0
            or len(items) > 20
            or len(source_text) > 12000
        ):
            raise ContentError("invalid_memory", "候选提交输入无效", 422)
        payload = {
            "proposals": items,
            "options": options.model_dump(),
            "sequence": sequence,
            "source_text": source_text,
        }
        payload["signature"] = fingerprint(payload)
        with self.database.transaction() as db:
            if db.execute(
                "SELECT 1 FROM memory_learning_jobs WHERE job_id=?", (job_id,)
            ).fetchone():
                task = self._row(db, job_id)
                if task["payload"].get("signature") != payload["signature"]:
                    raise ContentError("memory_source_conflict", "同一任务不能改绑候选", 409)
                withdrawn = db.execute(
                    "SELECT withdrawn FROM acceptances WHERE source_hash=?",
                    (fingerprint(job_id),),
                ).fetchone()
                if withdrawn and withdrawn[0] and task["state"] not in {"completed", "cancelled"}:
                    committed = self._committed(db, task)
                    return self._terminal(
                        db,
                        task,
                        "completed" if committed else "cancelled",
                        None if committed else {"code": "memory_learning_cancelled"},
                    )
                return self._summary(task)
            accepted = db.execute(
                "SELECT sequence,signature,withdrawn FROM acceptances WHERE source_hash=?",
                (fingerprint(job_id),),
            ).fetchone()
            expected = fingerprint(
                {"session_id": options.session_id, "scope": options.scope, "text": source_text}
            )
            if not accepted or accepted[:2] != (sequence, expected):
                raise ContentError("memory_source_conflict", "候选未绑定已受理输入", 409)
            if accepted[2]:
                raise ContentError(
                    "memory_learning_cancelled", "该受理来源已取消或撤回，不能重新提交候选", 409
                )
            self._capacity(db)
            at = datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO memory_learning_jobs VALUES (?,?,?,?,?,?,?)",
                (job_id, "queued", 0, at, at, json.dumps(payload, ensure_ascii=False), "{}"),
            )
            self._prune_history(db)
            return self._summary(self._row(db, job_id))

    def claim(self):
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT job_id FROM memory_learning_jobs WHERE state='queued' AND attempts<? ORDER BY rowid LIMIT 1",
                (MAX_ATTEMPTS,),
            ).fetchone()
            if not row:
                return None
            task = self._row(db, row[0])
            task.update(
                state="running",
                attempts=task["attempts"] + 1,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._save(db, task)
            return {
                "job_id": task["job_id"],
                "attempt": task["attempts"],
                **{
                    k: task["payload"][k]
                    for k in ("proposals", "options", "sequence", "source_text")
                },
            }

    def _committed(self, db, task):
        effect = task["payload"].get("write_result")
        if effect is None:
            return False
        retained = self.database.committed_entries(db, task["job_id"], datetime.now(UTC)) or set()
        task["summary"].update(
            entry_ids=sorted(retained),
            promoted_ids=sorted(retained.intersection(effect["promoted_ids"])),
            counts_known=True,
            counts={
                name: len(effect[name + "_ids"]) for name in ("created", "reinforced", "promoted")
            },
            skipped_proposals=effect.get("skipped_proposals", []),
        )
        return True

    def _terminal(self, db, task, state, error=None):
        task.update(state=state, updated_at=datetime.now(UTC).isoformat())
        task["summary"]["error"] = safe_error(error)
        history = task["summary"].setdefault("attempt_history", [])
        if task["attempts"] and not any(a["attempt"] == task["attempts"] for a in history):
            history.append(
                {
                    "attempt": task["attempts"],
                    "state": state,
                    "error": task["summary"]["error"],
                    "usage": {},
                    "usage_unknown": False,
                }
            )
        if state in {"completed", "cancelled", "skipped"} or task["attempts"] >= MAX_ATTEMPTS:
            task["payload"].pop("source_text", None)
            task["payload"].pop("proposals", None)
        self._save(db, task)
        self._prune_history(db)
        return self._summary(task)

    def finish(self, job_id, state, *, usage=None, error=None):
        if usage is not None and usage.model_dump().get("model_calls", 0):
            raise ContentError("invalid_memory", "确定性记忆提交不能记入模型调用", 422)
        if state not in {"completed", "failed", "skipped"}:
            raise ContentError("invalid_memory", "无效记忆提交终态", 422)
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if task["state"] in TERMINAL:
                return self._summary(task)
            if task["state"] != "running":
                raise ContentError("memory_learning_conflict", "任务未运行", 409)
            if self._committed(db, task):
                state, error = "completed", None
            elif state == "completed":
                raise ContentError("memory_learning_conflict", "候选尚未提交，不能报告完成", 409)
            return self._terminal(db, task, state, error)

    def cancel(self, job_id, reason="memory_learning_cancelled"):
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if task["state"] in TERMINAL:
                return self._summary(task)
            committed = self._committed(db, task)
            if not committed:
                # Keep cancellation beside the durable accepted source, not only
                # in bounded queue history. A stale foreground record cannot
                # resurrect this source after its cancelled task is pruned.
                db.execute(
                    "UPDATE acceptances SET withdrawn=1 WHERE source_hash=?",
                    (fingerprint(job_id),),
                )
            return self._terminal(
                db,
                task,
                "completed" if committed else "cancelled",
                None if committed else {"code": "memory_learning_cancelled"},
            )

    def retry(self, job_id, revision=None):
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if not self._summary(task)["can_retry"]:
                raise ContentError("memory_learning_conflict", "任务不能重试或已达上限", 409)
            withdrawn = db.execute(
                "SELECT withdrawn FROM acceptances WHERE source_hash=?",
                (fingerprint(job_id),),
            ).fetchone()
            if withdrawn and withdrawn[0]:
                return self._terminal(db, task, "cancelled", {"code": "memory_learning_cancelled"})
            self._capacity(db)
            task.update(state="queued", updated_at=datetime.now(UTC).isoformat())
            task["summary"]["error"] = None
            self._save(db, task)
            return self._summary(task)

    def recover(self):
        result = {"completed": 0, "failed": 0}
        with self.database.transaction() as db:
            ids = db.execute(
                "SELECT job_id FROM memory_learning_jobs WHERE state='running'"
            ).fetchall()
            for (job_id,) in ids:
                task = self._row(db, job_id)
                committed = self._committed(db, task)
                state = "completed" if committed else "failed"
                self._terminal(
                    db,
                    task,
                    state,
                    None if committed else {"code": "interrupted", "retryable": True},
                )
                result[state] += 1
        return result

    def get(self, job_id):
        with self.database.transaction() as db:
            return self._summary(self._row(db, job_id))

    def list(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY:
            raise ContentError("invalid_memory", "列表上限应为1到200", 422)
        with self.database.transaction() as db:
            ids = db.execute(
                "SELECT job_id FROM memory_learning_jobs ORDER BY updated_at DESC,rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            counts = dict.fromkeys(STATES, 0)
            counts.update(
                dict(db.execute("SELECT state,COUNT(*) FROM memory_learning_jobs GROUP BY state"))
            )
            return {"tasks": [self._summary(self._row(db, i)) for (i,) in ids], "counts": counts}

    def cleanup(self):
        with self.database.transaction() as db:
            removed = self._prune_history(db)
            return {
                "removed": removed,
                "remaining": db.execute("SELECT COUNT(*) FROM memory_learning_jobs").fetchone()[0],
            }
