"""Migration and transactional rollback preserve source and receipt semantics."""

import json
import sqlite3

import pytest

from manimagent.memory.learning import LearningStore
from manimagent.memory.store import MemoryStore
from tests.support.memory import pending


def test_legacy_values_are_exported_and_never_injected(tmp_path):
    path = tmp_path / "memory.sqlite3"
    body = {"field": "视觉偏好", "value": "以前自由文本", "temporary": True}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY,body TEXT NOT NULL)")
        db.execute("INSERT INTO entries VALUES (?,?)", ("old", json.dumps(body)))
        db.execute(
            "CREATE TABLE memory_learning_jobs (job_id TEXT PRIMARY KEY,state TEXT NOT NULL,attempts INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL,summary TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO memory_learning_jobs VALUES (?,?,?,?,?,?,?)",
            ("oldjob", "queued", 0, "old", "old", '{"text":"旧用户原话"}', "{}"),
        )
    store = MemoryStore(path)
    assert not store.snapshot("s")["entries"]
    report = json.loads((tmp_path / "memory-migration-v3.json").read_text(encoding="utf-8"))
    assert json.loads(report["tables"]["entries"][0]["body"]) == body
    assert LearningStore(path).get("oldjob")["state"] == "cancelled"
    assert not LearningStore(path).get("oldjob")["can_retry"]
    assert not MemoryStore(path).list_entries()


def test_receipt_failure_rolls_back_entries_observations_and_source(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite3"
    memory, queue = MemoryStore(path), LearningStore(path)
    task = pending(memory, source="job1")
    queue.enqueue(
        "job1", task["proposals"], {"session_id": "s1"}, task["sequence"], task["source_text"]
    )
    queue.claim()

    def fail(*args):
        raise OSError("receipt failure")

    with monkeypatch.context() as faults:
        faults.setattr(memory.database, "record_learning_write", fail)
        with pytest.raises(OSError, match="receipt"):
            memory.observe_proposals(**task, learning_job_id="job1")
    with sqlite3.connect(path) as db:
        assert all(
            db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
            for table in ("entries", "observations", "sources")
        )
        assert db.execute("SELECT COUNT(*) FROM acceptances").fetchone()[0] == 1
    memory.observe_proposals(**task, learning_job_id="job1")
    assert queue.recover() == {"completed": 1, "failed": 0}


def test_migration_export_failure_keeps_old_database(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY,body TEXT NOT NULL)")
        db.execute("INSERT INTO entries VALUES (?,?)", ("old", "{}"))

    def fail(*args):
        raise OSError("export failed")

    monkeypatch.setattr("manimagent.memory.database.write_json", fail)
    with pytest.raises(OSError, match="export"):
        MemoryStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_legacy_migration_requires_exclusive_data_directory_ownership(tmp_path):
    from manimagent.errors import ContentError
    from manimagent.instance import InstanceLease

    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY,body TEXT NOT NULL)")
        db.execute("INSERT INTO entries VALUES ('old','{}')")
    lease = InstanceLease(tmp_path)
    lease.acquire()
    try:
        with pytest.raises(ContentError) as error:
            MemoryStore(path)
        assert error.value.error.code == "instance_busy"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
            assert not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_metadata'"
            ).fetchone()
        assert not (tmp_path / "memory-migration-v3.json").exists()
    finally:
        lease.release()
    assert not MemoryStore(path).list_entries()
    # Migration releases its temporary lease and ordinary v3 reads need no new owner.
    lease.acquire()
    try:
        assert not MemoryStore(path).list_entries()
    finally:
        lease.release()
