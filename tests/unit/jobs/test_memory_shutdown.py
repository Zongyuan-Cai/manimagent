"""Event-controlled lifetime and cross-storage acceptance regressions."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import pytest

from manimagent.jobs.manager import JobManager
from manimagent.schemas import Job, TextGenerationRequest
from tests.support.animation import config


def learning_request():
    return TextGenerationRequest(
        source={
            "type": "inputs",
            "instruction": "请详细讲解",
            "inputs": [{"type": "text", "text": "已知x=1，计算x+x。"}],
        },
        memory={"session_id": "shutdown"},
    )


def probe_lease_in_another_process(root):
    source = """
import json, sys
from pathlib import Path
from manimagent.errors import ContentError
from manimagent.instance import InstanceLease
lease = InstanceLease(Path(sys.argv[1]))
try:
    lease.acquire()
except ContentError as exc:
    print(json.dumps({'error': exc.error.code}))
else:
    lease.release()
    print(json.dumps({'acquired': True}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source, str(root)], capture_output=True, text=True, timeout=5
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_stop_retains_process_lease_until_inflight_memory_commit_finishes(tmp_path, monkeypatch):
    from manimagent.personalization import MemoryProposal

    settings = config(tmp_path)
    manager = JobManager(settings)
    entered, release = threading.Event(), threading.Event()
    original_commit = manager.memory.observe_proposals
    source_id = "queued-memory"
    accepted = manager.memory.accept(source_id, "shutdown", "请详细讲解")
    manager.learning.enqueue(
        source_id,
        [MemoryProposal(field="explanation.detail", value="detailed", quote="请详细讲解")],
        {"session_id": "shutdown"},
        accepted["sequence"],
        "请详细讲解",
    )

    def blocked_commit(*args, **kwargs):
        entered.set()
        assert release.wait(15), "Test did not release in-flight SQLite commit"
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(manager.memory, "observe_proposals", blocked_commit)
    try:
        manager.start()
        manager.start()
        assert entered.wait(5)
        manager.stop()
        assert manager.learning_thread.is_alive() and manager.thread.is_alive()
        assert probe_lease_in_another_process(manager.store.root) == {"error": "instance_busy"}
        release.set()
        manager.thread.join(5)
        assert not manager.thread.is_alive() and not manager.learning_thread.is_alive()
        task = manager.learning.get(source_id)
        assert task["state"] == "completed"
        assert task["usage"] == {} and not task["usage_unknown"]
        assert manager.memory.list_entries()[0].value == "detailed"
        assert probe_lease_in_another_process(manager.store.root) == {"acquired": True}
    finally:
        release.set()
        manager.stop()
        if manager.thread.is_alive():
            manager.thread.join(5)


def test_acceptance_does_not_enqueue_uninterpreted_user_text(tmp_path, monkeypatch):
    manager = JobManager(config(tmp_path))
    inside_accept, release = threading.Event(), threading.Event()
    saved, failures = [], []
    original_save = manager.store.save

    def save(kind, identifier, body):
        if kind == "records":
            inside_accept.set()
            assert release.wait(5)
        return original_save(kind, identifier, body)

    monkeypatch.setattr(manager.store, "save", save)

    def accept():
        try:
            saved.append(manager.submit("generation", learning_request()))
        except Exception as exc:
            failures.append(exc)

    accepted_thread = threading.Thread(target=accept)
    accepted_thread.start()
    try:
        assert inside_accept.wait(3)
        assert manager.learning.list()["tasks"] == []
    finally:
        release.set()
        accepted_thread.join(5)
    assert not failures and len(saved) == 1
    assert manager.store.load("jobs", saved[0].job_id, Job).status == "queued"
    assert manager.learning.list()["tasks"] == []
    manager.stop()


def test_failed_acceptance_releases_capacity_without_creating_learning_task(tmp_path, monkeypatch):
    settings = config(tmp_path)
    settings.max_waiting = 0
    manager = JobManager(settings)
    original_save = manager.store.save

    def fail_record(kind, identifier, body):
        if kind == "records":
            raise OSError("controlled record failure")
        return original_save(kind, identifier, body)

    with monkeypatch.context() as faults:
        faults.setattr(manager.store, "save", fail_record)
        with pytest.raises(OSError):
            manager.submit("generation", learning_request())
    assert manager.learning.list()["tasks"] == []
    request = learning_request().model_copy(update={"memory": None})
    accepted = manager.submit("generation", request)
    assert accepted.status == "queued"
