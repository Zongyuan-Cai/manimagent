import time
from pathlib import Path

from manimagent.content_store import ContentStore

ROOT = Path(__file__).resolve().parents[2]


def wait_job(client, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/jobs/{identifier}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError("文字任务没有完成")


def store_for(settings):
    return ContentStore(
        settings.private_dir or settings.runs_dir / "private", settings.max_upload_bytes
    )
