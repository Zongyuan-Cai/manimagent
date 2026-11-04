"""Shared persistence and bounded polling for opt-in live acceptance scripts."""

from __future__ import annotations

import hashlib
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


ROOT = Path(__file__).resolve().parents[1]
KEY_ENV = "MANIMAGENT_REVIEW_DEEPSEEK_KEY"


def source_hash(root: Path) -> str:
    """Bind a benchmark to nested sources, cases, fixtures and its dependency lock."""
    paths = [root / "uv.lock", root / "pyproject.toml"]
    for directory in ("src/manimagent", "evals", "tests/support"):
        paths.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file() and path.suffix in {".py", ".js", ".json", ".css", ".html"}
        )
    digest = hashlib.sha256()
    for path in sorted(paths):
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        for part in (name, content):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()
