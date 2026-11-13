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


def legacy_model_memory(snapshot: dict | None, role: str) -> dict[str, list[dict]]:
    """Read-only projection for auditing pre-rewrite evaluation evidence.

    The snapshot owns validity and conflict resolution. IDs, evidence, observation
    timestamps and session bookkeeping belong only to that audit record. Roles
    filter known irrelevant fields conservatively; custom fields remain intact.
    """
    result: dict[str, list[dict]] = {"deep": [], "recent": []}
    if snapshot is None or role in {"material_reader", "memory_extractor"}:
        return result
    for entry in snapshot["entries"]:
        field = entry["field"]
        if role in {"answer_writer", "answer_checker"} and field.startswith("visual."):
            continue
        item = {key: entry[key] for key in ("field", "value", "scope")}
        if entry["layer"] == "deep":
            result["deep"].append(item)
        else:
            # Keep the difference between a current-session exception and a
            # recent, unconfirmed preference, without injecting changing dates.
            item["temporary"] = entry.get("temporary", False)
            result["recent"].append(item)
    for entries in result.values():
        entries.sort(key=lambda item: (item["scope"], item["field"], item["value"]))
    return result
