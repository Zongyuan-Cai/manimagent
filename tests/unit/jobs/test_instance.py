from __future__ import annotations

import json
import subprocess
import sys

import pytest

from manimagent.config import Settings
from manimagent.content_store import new_id
from manimagent.errors import ContentError
from manimagent.instance import InstanceLease
from manimagent.jobs.manager import JobManager
from manimagent.jobs.state import save_job
from manimagent.schemas import Job


def test_second_process_cannot_recover_live_jobs_and_crash_releases_lock(tmp_path):
    # A real separate interpreter owns the OS lock. Killing it must release ownership.
    code = """
import json, sys
from pathlib import Path
from manimagent.instance import InstanceLease
lease = InstanceLease(Path(sys.argv[1]))
lease.acquire()
print(json.dumps({'locked': True}), flush=True)
sys.stdin.read()
"""
    cfg = Settings(runs_dir=tmp_path)
    manager = JobManager(cfg)
    job = Job(job_id=new_id("job"), kind="parse", status="running")
    save_job(manager.store, job)
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(manager.store.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert json.loads(process.stdout.readline())["locked"]
        with pytest.raises(ContentError, match="已有服务"):
            manager.start()
        assert manager.store.load("jobs", job.job_id, Job).status == "running"
    finally:
        process.kill()
        process.communicate(timeout=10)
    manager.start()
    try:
        assert manager.store.load("jobs", job.job_id, Job).status == "interrupted"
        manager.start()  # Launcher + ASGI lifespan is idempotent.
    finally:
        manager.stop()
    lease = InstanceLease(manager.store.root)
    lease.acquire()
    lease.release()
