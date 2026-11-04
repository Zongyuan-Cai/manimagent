"""Shared persistence and bounded polling for opt-in live acceptance scripts."""

from __future__ import annotations

import json
import time
from pathlib import Path


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="",
    )


def wait_job(client, identifier: str, timeout: float = 300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/jobs/{identifier}")
        assert response.status_code == 200, f"poll HTTP {response.status_code}"
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.15)
    raise TimeoutError("Job exceeded live test deadline")
