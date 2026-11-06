"""Event-controlled lifetime and cross-storage acceptance regressions."""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import httpx
import pytest

from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ChatProvider
from manimagent.schemas import Job, TextGenerationRequest
from tests.support.animation import config, transport


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


def test_stop_retains_process_lease_until_inflight_memory_response_finishes(tmp_path):
    settings = config(tmp_path)
    base = transport(settings, [])
    entered, release = threading.Event(), threading.Event()

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        if "user_text" not in payload:
            return base.transport.handler(request)
        entered.set()
        assert release.wait(15), "Test did not release the in-flight memory response"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "field": "explanation.detail",
                                            "value": "详细讲解",
                                            "evidence": "请详细讲解",
                                        }
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
        )

    manager = JobManager(settings, ChatProvider(settings, httpx.MockTransport(handler)))
    try:
        manager.start()
        manager.start()  # The local launcher and ASGI lifespan can both call start.
        accepted = manager.submit("generation", learning_request())
        assert entered.wait(5)
        foreground = manager.store.load("jobs", accepted.job_id, Job)
        assert foreground.status == "completed"
        manager.stop()  # Its three-second join expires while the HTTP response is held.
        assert manager.learning_thread.is_alive() and manager.thread.is_alive()
        assert probe_lease_in_another_process(manager.store.root) == {"error": "instance_busy"}
        release.set()
        manager.thread.join(5)
        assert not manager.thread.is_alive() and not manager.learning_thread.is_alive()
        task = manager.learning.get(accepted.job_id)
        assert task["state"] == "failed" and task["error"]["code"] == "interrupted"
        assert task["usage"]["model_calls"] == 1 and task["usage"]["input_tokens"] == 50
        assert manager.memory.list_entries() == []
        assert manager.store.load("jobs", accepted.job_id, Job).usage == foreground.usage
        assert probe_lease_in_another_process(manager.store.root) == {"acquired": True}
    finally:
        release.set()
        manager.stop()
        if manager.thread.is_alive():
            manager.thread.join(5)


def test_reconciliation_waits_for_acceptance_to_publish_parent_json(tmp_path, monkeypatch):
    manager = JobManager(config(tmp_path))
    inside_accept, release = threading.Event(), threading.Event()
    reconcile_started, reconcile_entered = threading.Event(), threading.Event()
    saved, failures = [], []
    original_save = manager.store.save
    original_reconcile = manager.learning_worker._reconcile

    def save(kind, identifier, body):
        if kind == "records":
            inside_accept.set()
            assert release.wait(5), "Test did not release parent JSON publication"
        return original_save(kind, identifier, body)

    def reconcile(job_id):
        reconcile_entered.set()
        return original_reconcile(job_id)

    monkeypatch.setattr(manager.store, "save", save)
    monkeypatch.setattr(manager.learning_worker, "_reconcile", reconcile)

    def accept():
        try:
            saved.append(manager.submit("generation", learning_request()))
        except Exception as exc:
            failures.append(exc)

    accepted_thread = threading.Thread(target=accept)
    accepted_thread.start()
    reconcile_thread = None
    try:
        assert inside_accept.wait(3)
        task = manager.learning.list()["tasks"][0]
        assert task["state"] == "waiting"
        assert not (manager.store.root / "jobs" / f"{task['job_id']}.json").exists()

        def check_parent():
            reconcile_started.set()
            manager.learning_worker.reconcile(task["job_id"])

        reconcile_thread = threading.Thread(target=check_parent)
        reconcile_thread.start()
        assert reconcile_started.wait(3)
        assert not reconcile_entered.wait(0.1)  # Acceptance holds the shared manager lock.
        manager.learning.cleanup()
        assert manager.learning.get(task["job_id"])["state"] == "waiting"
    finally:
        release.set()
        accepted_thread.join(5)
        if reconcile_thread is not None:
            reconcile_thread.join(5)
    assert not failures and len(saved) == 1
    assert reconcile_entered.is_set()
    assert manager.store.load("jobs", saved[0].job_id, Job).status == "queued"
    assert manager.learning.get(saved[0].job_id)["state"] == "waiting"


def test_failed_acceptance_releases_capacity_even_if_learning_rollback_also_fails(
    tmp_path, monkeypatch
):
    settings = config(tmp_path)
    settings.max_waiting = 0
    manager = JobManager(settings)
    original_save = manager.store.save

    def fail_record(kind, identifier, body):
        if kind == "records":
            raise OSError("controlled record failure")
        return original_save(kind, identifier, body)

    def fail_cancel(*_args):
        raise OSError("controlled SQLite cleanup failure")

    with monkeypatch.context() as faults:
        faults.setattr(manager.store, "save", fail_record)
        faults.setattr(manager.learning, "cancel", fail_cancel)
        with pytest.raises(OSError):
            manager.submit("generation", learning_request())
    request = learning_request().model_copy(update={"memory": None})
    accepted = manager.submit("generation", request)
    assert accepted.status == "queued"
