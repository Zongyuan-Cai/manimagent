import json
from pathlib import Path

import pytest

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.materials.process import parse_isolated
from manimagent.schemas import Usage


def test_budget_failure_immediately_after_spawn_still_reaps_child(tmp_path, monkeypatch):
    events = []

    class Process:
        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self):
            events.append("wait")

    class Budget:
        def remaining(self):
            raise BudgetExceeded("expired")

    monkeypatch.setattr("manimagent.materials.process.subprocess.Popen", lambda *a, **k: Process())
    with pytest.raises(BudgetExceeded, match="expired"):
        parse_isolated({}, tmp_path, 10, Budget())
    assert events == ["kill", "wait"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("returncode", [0, 1])
def test_missing_worker_result_is_an_execution_failure(tmp_path, monkeypatch, returncode):
    class Process:
        def poll(self):
            return returncode

        def wait(self):
            return returncode

    def spawn(*args, **kwargs):
        kwargs["stderr"].write(b"RuntimeError: parser implementation failed")
        return Process()

    monkeypatch.setattr("manimagent.materials.process.subprocess.Popen", spawn)
    message = "without writing a result" if returncode == 0 else "parser implementation failed"
    with pytest.raises(RuntimeError, match=message) as caught:
        parse_isolated({}, tmp_path, 10, Budget(Settings(), Usage()))
    assert not isinstance(caught.value, ContentError)
    assert list(tmp_path.iterdir()) == []


def test_failed_worker_cannot_publish_a_result(tmp_path, monkeypatch):
    class Process:
        def poll(self):
            return 1

        def wait(self):
            return 1

    def spawn(args, **kwargs):
        Path(args[-1]).write_text(json.dumps({"blocks": []}), encoding="utf-8")
        kwargs["stderr"].write(b"failure after writing output")
        return Process()

    monkeypatch.setattr("manimagent.materials.process.subprocess.Popen", spawn)
    with pytest.raises(RuntimeError, match="failure after writing output"):
        parse_isolated({}, tmp_path, 10, Budget(Settings(), Usage()))


def test_worker_preserves_expected_errors_and_exposes_unexpected_failures(tmp_path):
    task = {
        "path": str(tmp_path / "input"),
        "media_type": "application/unsupported",
        "selection": None,
        "index": 0,
        "file_id": "file_test",
        "limits": {},
    }
    with pytest.raises(ContentError) as caught:
        parse_isolated(task, tmp_path, 10, Budget(Settings(), Usage()))
    assert caught.value.error.code == "unsupported_file_input"
    assert caught.value.http_status == 415

    task["unexpected_argument"] = True
    with pytest.raises(RuntimeError, match="unexpected_argument") as caught:
        parse_isolated(task, tmp_path, 10, Budget(Settings(), Usage()))
    assert not isinstance(caught.value, ContentError)


@pytest.mark.parametrize(
    "media_type,data",
    [("application/pdf", b"%PDF-1.7\n"), ("image/png", b"\x89PNG\r\n\x1a\n")],
)
def test_corrupt_document_bytes_still_request_replacement_input(tmp_path, media_type, data):
    path = tmp_path / "input"
    path.write_bytes(data)
    settings = Settings()
    task = {
        "path": str(path),
        "media_type": media_type,
        "selection": None,
        "index": 0,
        "file_id": "file_test",
        "limits": {"max_archive_bytes": settings.max_archive_bytes},
    }
    with pytest.raises(ContentError) as caught:
        parse_isolated(task, tmp_path, 10, Budget(settings, Usage()))
    assert caught.value.error.code == "invalid_file"
    assert caught.value.state == "needs_input"
