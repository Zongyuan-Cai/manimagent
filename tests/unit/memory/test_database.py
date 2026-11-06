"""Cross-store persistence must preserve migrations and atomic write receipts."""

import json
import sqlite3

import pytest

from manimagent.memory.learning import LearningStore
from manimagent.memory.models import MemoryCandidate
from manimagent.memory.store import MemoryStore
from tests.support.memory import observe


def test_learning_store_initializes_and_migrates_same_database_without_entry_policy(tmp_path):
    path = tmp_path / "memory.sqlite3"
    # The first memory format predates the input replay signature/session order.
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE sources (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, entry_ids TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE sessions (entry_id TEXT NOT NULL, session_id TEXT NOT NULL, "
            "last_seen TEXT NOT NULL, PRIMARY KEY(entry_id,session_id), "
            "FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE)"
        )
    learning = LearningStore(path)
    memory = MemoryStore(path)
    entry = observe(memory)
    memory.confirm(entry.memory_id)
    learning.enqueue("job-one", "用户原话", {"session_id": "s1"}, memory.revision())
    assert LearningStore(path).get("job-one")["state"] == "waiting"
    assert MemoryStore(path).snapshot("s1")["manual_revision"] == 1
    memory.delete(entry.memory_id)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert learning.get("job-one")["state"] == "waiting"


def test_receipt_failure_rolls_back_entries_source_and_session_together(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite3"
    memory, learning = MemoryStore(path), LearningStore(path)
    learning.enqueue("job-one", "详细讲解", {"session_id": "s1"}, 0)
    learning.activate("job-one")
    learning.claim()

    def write():
        return memory.observe(
            [MemoryCandidate(field="explanation.detail", value="详细", evidence="详细讲解")],
            session_id="s1",
            source_id="job-one",
            source_text="详细讲解",
            expected_revision=0,
            learning_job_id="job-one",
        )

    def fail_receipt(*_args):
        raise OSError("controlled receipt write failure")

    with monkeypatch.context() as faults:
        faults.setattr(memory.database, "record_learning_write", fail_receipt)
        with pytest.raises(OSError, match="receipt"):
            write()
    with sqlite3.connect(path) as db:
        for table in ("entries", "sources", "sessions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        payload = json.loads(db.execute("SELECT payload FROM memory_learning_jobs").fetchone()[0])
        assert "write_result" not in payload
    entry = write()[0]
    assert LearningStore(path).recover() == {"completed": 1, "failed": 0}
    result = learning.get("job-one")
    assert result["entry_ids"] == [entry.memory_id]
    assert result["counts"] == {"created": 1, "reinforced": 0, "promoted": 0}
