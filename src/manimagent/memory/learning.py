"""Durable, bounded learning tasks; this module never invokes a model provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from manimagent.errors import ContentError
from manimagent.schemas import Usage

from .database import MemoryDatabase, fingerprint
from .models import LearningInput

STATES = ("waiting", "queued", "running", "completed", "failed", "skipped", "cancelled")
ACTIVE = {"waiting", "queued", "running"}
TERMINAL = set(STATES) - ACTIVE
MAX_PENDING = 100
MAX_HISTORY = 200
MAX_ATTEMPTS = 3


def safe_error(error: dict | None) -> dict | None:
    if error is None:
        return None
    code = error["code"]
    messages = {
        "memory_changed": "记忆已被手动修改，未写入旧的提取结果",
        "memory_learning_cancelled": "后台记忆任务已取消",
        "interrupted": "后台任务被服务重启中断，调用用量可能尚未返回，请按需手动重试",
        "provider_auth_error": "记忆模型鉴权失败，请检查核心 API 设置",
        "model_unavailable": "记忆模型尚未配置或不可用",
        "provider_rate_limited": "记忆模型请求受到限流",
        "provider_quota_exceeded": "记忆模型额度不足",
        "provider_timeout": "记忆模型连接失败或超时",
        "provider_unavailable": "记忆模型服务暂不可用",
        "budget_exceeded": "后台记忆任务达到预算上限",
        "memory_budget_exceeded": "后台记忆任务达到预算上限",
        "memory_budget_limit": "后台记忆任务超过独立用量预算上限",
        "memory_context_limit": "记忆提取上下文超过模型预算",
        "memory_internal_error": "后台记忆沉淀未完成，请检查服务日志",
        "ungrounded_memory": "记忆候选缺少用户原句依据，未保存",
        "invalid_provider_response": "记忆模型没有返回有效结构",
        "output_truncated": "记忆模型输出被截断",
        "answer_not_published": "正文未成功发布，未进行后台记忆学习",
        "memory_capacity": "记忆存储容量已满",
    }
    # Never echo arbitrary provider error bodies, user text or credential strings.
    if not isinstance(code, str) or code not in messages:
        code = "memory_learning_failed"
    return {
        "code": code,
        "message": messages.get(code, "后台记忆沉淀未完成，请检查配置后按需重试"),
        "retryable": bool(error.get("retryable", False)),
    }


def add_usage(total: dict, current: dict) -> dict:
    result = dict(total)
    for key, value in current.items():
        if key == "tokens_reported":
            result[key] = bool(result.get(key)) or value
        elif value is None:
            result.setdefault(key, None)
        else:
            result[key] = (result.get(key) or 0) + value
    return result


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
        if row is None:
            raise ContentError("memory_learning_not_found", "后台记忆任务不存在或已清理", 404)
        return {
            "job_id": row[0],
            "state": row[1],
            "attempts": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "payload": json.loads(row[5]),
            "summary": json.loads(row[6]),
        }

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
            **task["summary"],
            **{
                key: task[key]
                for key in ("job_id", "state", "attempts", "created_at", "updated_at")
            },
            "max_attempts": MAX_ATTEMPTS,
            "can_retry": task["state"] == "failed"
            and task["attempts"] < MAX_ATTEMPTS
            and "text" in task["payload"],
            "can_cancel": task["state"] in ACTIVE,
        }

    @staticmethod
    def _capacity(db):
        count = db.execute(
            "SELECT COUNT(*) FROM memory_learning_jobs WHERE state IN ('waiting','queued','running')"
        ).fetchone()[0]
        # A cancelled model call can still be in flight. Its settlement record
        # occupies a slot until finish/recovery, so protected history is bounded.
        count += sum(
            bool(json.loads(row[0]).get("usage_pending"))
            for row in db.execute(
                "SELECT payload FROM memory_learning_jobs WHERE state IN ('completed','failed','skipped','cancelled')"
            )
        )
        if count >= MAX_PENDING:
            raise ContentError("memory_learning_capacity", "后台记忆任务队列已满，请稍后重试", 429)

    @staticmethod
    def _prune_history(db):
        rows = db.execute(
            "SELECT job_id,payload FROM memory_learning_jobs WHERE state IN ('completed','failed','skipped','cancelled') ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        settled = [(job_id,) for job_id, body in rows if not json.loads(body).get("usage_pending")]
        allowance = MAX_HISTORY - (len(rows) - len(settled))
        removed = settled[max(0, allowance) :]
        db.executemany("DELETE FROM memory_learning_jobs WHERE job_id=?", removed)
        return len(removed)

    def enqueue(self, job_id, text, options, revision):
        try:
            item = LearningInput(job_id=job_id, text=text, options=options, revision=revision)
        except ValidationError as exc:
            raise ContentError("invalid_memory", "后台记忆任务输入无效或超过 12000 字限制") from exc
        body = item.model_dump(exclude={"job_id"})
        signature = fingerprint({"text": body["text"], "options": body["options"]})
        with self.database.transaction() as db:
            if db.execute(
                "SELECT 1 FROM memory_learning_jobs WHERE job_id=?", (job_id,)
            ).fetchone():
                task = self._row(db, job_id)
                if task["payload"]["signature"] != signature:
                    raise ContentError(
                        "memory_source_conflict", "同一任务不能改绑其他记忆输入", 409
                    )
                return self._summary(task)
            self._capacity(db)
            at = datetime.now(UTC).isoformat()
            payload = {**body, "signature": signature}
            summary = {
                "entry_ids": [],
                "promoted_ids": [],
                "counts": {"created": 0, "reinforced": 0, "promoted": 0},
                "counts_known": False,
                "usage": {},
                "usage_unknown": False,
                "attempt_history": [],
                "error": None,
            }
            db.execute(
                "INSERT INTO memory_learning_jobs VALUES (?,?,?,?,?,?,?)",
                (
                    job_id,
                    "waiting",
                    0,
                    at,
                    at,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(summary),
                ),
            )
            self._prune_history(db)
            return self._summary(self._row(db, job_id))

    def activate(self, job_id):
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if task["state"] == "waiting":
                task["state"] = "queued"
                task["updated_at"] = datetime.now(UTC).isoformat()
                self._save(db, task)
            return self._summary(task)

    def claim(self):
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT job_id FROM memory_learning_jobs WHERE state='queued' AND attempts<? ORDER BY created_at,rowid LIMIT 1",
                (MAX_ATTEMPTS,),
            ).fetchone()
            if row is None:
                return None
            task = self._row(db, row[0])
            task["state"] = "running"
            task["attempts"] += 1
            task["updated_at"] = datetime.now(UTC).isoformat()
            self._save(db, task)
            return {
                "job_id": task["job_id"],
                "attempt": task["attempts"],
                **{key: task["payload"][key] for key in ("text", "options", "revision")},
            }

    def has_queued(self) -> bool:
        with self.database.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM memory_learning_jobs WHERE state='queued' LIMIT 1"
                ).fetchone()
                is not None
            )

    def _committed(self, db, task):
        retained = self.database.committed_entries(db, task["job_id"], datetime.now(UTC))
        if retained is None:
            return False
        effect = task["payload"].get("write_result")
        task["summary"]["entry_ids"] = sorted(retained)
        task["summary"]["counts_known"] = effect is not None or not retained
        task["summary"]["promoted_ids"] = (
            sorted(retained.intersection(effect["promoted_ids"])) if effect else []
        )
        task["summary"]["counts"] = {
            name: len(retained.intersection(effect[name + "_ids"])) if effect else 0
            for name in ("created", "reinforced", "promoted")
        }
        return True

    def _terminal(
        self,
        db,
        task,
        state,
        usage: Usage | None = None,
        error=None,
        *,
        unknown=False,
        awaiting_usage=False,
    ):
        task["state"] = state
        task["updated_at"] = datetime.now(UTC).isoformat()
        if awaiting_usage:
            task["payload"]["usage_pending"] = True
        else:
            task["payload"].pop("usage_pending", None)
        # Provider values are normalized before accumulating into the worker's Usage.
        clean = {} if usage is None else usage.model_dump()
        unknown = (
            unknown
            or not clean
            or bool(clean.get("model_calls", 0) and not clean.get("tokens_reported"))
        )
        error = safe_error(error)
        task["summary"]["error"] = error
        history = task["summary"]["attempt_history"]
        prior = next((item for item in history if item["attempt"] == task["attempts"]), None)
        if task["attempts"] and prior is None:
            history.append(
                {
                    "attempt": task["attempts"],
                    "state": state,
                    "usage": clean,
                    "usage_unknown": unknown,
                    "error": error,
                }
            )
        elif prior is not None and not prior["usage"] and clean:
            # Cancellation may win the write race while a provider response is
            # still in flight. Account for its returned cost exactly once.
            prior.update(usage=clean, usage_unknown=unknown)
        task["summary"]["usage"] = {}
        for item in history:
            task["summary"]["usage"] = add_usage(task["summary"]["usage"], item["usage"])
        task["summary"]["usage_unknown"] = any(item["usage_unknown"] for item in history)
        if state in {"completed", "cancelled", "skipped"} or task["attempts"] >= MAX_ATTEMPTS:
            task["payload"].pop("text", None)
        self._save(db, task)
        self._prune_history(db)
        return self._summary(task)

    def finish(self, job_id, state, *, usage: Usage | None = None, error: dict | None = None):
        if state not in {"completed", "failed", "skipped"}:
            raise ContentError("invalid_memory", "后台记忆任务结束状态无效")
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if task["state"] in {"cancelled", "completed"}:
                return self._terminal(db, task, task["state"], usage, task["summary"]["error"])
            if task["state"] in TERMINAL:
                return self._summary(task)
            if task["state"] != "running":
                raise ContentError(
                    "memory_learning_conflict", "任务未在运行，不能写入完成状态", 409
                )
            committed = self._committed(db, task)
            if committed:
                state, error = "completed", None
            elif state == "completed":
                raise ContentError("memory_learning_conflict", "记忆尚未提交，不能标记完成", 409)
            return self._terminal(db, task, state, usage, error)

    def cancel(self, job_id, reason="memory_learning_cancelled"):
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if task["state"] in TERMINAL:
                return self._summary(task)
            committed = self._committed(db, task)
            return self._terminal(
                db,
                task,
                "completed" if committed else "cancelled",
                error=None
                if committed
                else {
                    "code": reason
                    if reason == "answer_not_published"
                    else "memory_learning_cancelled"
                },
                unknown=task["state"] == "running",
                awaiting_usage=task["state"] == "running",
            )

    def retry(self, job_id, revision):
        if type(revision) is not int or revision < 0:
            raise ContentError("invalid_memory", "记忆修订版本无效")
        with self.database.transaction() as db:
            task = self._row(db, job_id)
            if not self._summary(task)["can_retry"]:
                raise ContentError(
                    "memory_learning_conflict", "任务不能重试或已达到三次尝试上限", 409
                )
            self._capacity(db)
            task["state"] = "queued"
            task["updated_at"] = datetime.now(UTC).isoformat()
            task["payload"]["revision"] = revision
            task["summary"]["error"] = None
            self._save(db, task)
            return self._summary(task)

    def recover(self):
        result = {"completed": 0, "failed": 0}
        with self.database.transaction() as db:
            ids = db.execute(
                "SELECT job_id FROM memory_learning_jobs WHERE state='running' ORDER BY created_at,rowid"
            ).fetchall()
            for (job_id,) in ids:
                task = self._row(db, job_id)
                committed = self._committed(db, task)
                state = "completed" if committed else "failed"
                self._terminal(
                    db,
                    task,
                    state,
                    error=None
                    if committed
                    else {"code": "interrupted", "retryable": task["attempts"] < MAX_ATTEMPTS},
                    unknown=True,
                )
                result[state] += 1
            # A previous process cannot settle these cancelled/completed calls
            # after recovery acquires its lease. Keep their unknown cost visible,
            # but stop protecting them from ordinary history retention.
            for (job_id,) in db.execute(
                "SELECT job_id FROM memory_learning_jobs WHERE state IN ('completed','failed','skipped','cancelled')"
            ).fetchall():
                task = self._row(db, job_id)
                if task["payload"].pop("usage_pending", None):
                    self._save(db, task)
            self._prune_history(db)
        return result

    def get(self, job_id):
        with self.database.transaction() as db:
            return self._summary(self._row(db, job_id))

    def list(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY:
            raise ContentError("invalid_memory", "后台记忆列表数量必须为 1 到 200")
        with self.database.transaction() as db:
            ids = db.execute(
                "SELECT job_id FROM memory_learning_jobs ORDER BY updated_at DESC,rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            counts = dict.fromkeys(STATES, 0)
            counts.update(
                dict(db.execute("SELECT state,COUNT(*) FROM memory_learning_jobs GROUP BY state"))
            )
            return {
                "tasks": [self._summary(self._row(db, identifier)) for (identifier,) in ids],
                "counts": counts,
            }

    def waiting_ids(self) -> list[str]:
        with self.database.transaction() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT job_id FROM memory_learning_jobs WHERE state='waiting' ORDER BY created_at,rowid"
                )
            ]

    def cleanup(self):
        # Entry expiry/capacity belongs to the manager's configured MemoryStore.
        # The database layer has no entry eviction policy.
        with self.database.transaction() as db:
            removed = self._prune_history(db)
            remaining = db.execute("SELECT COUNT(*) FROM memory_learning_jobs").fetchone()[0]
            return {"removed": removed, "remaining": remaining}
