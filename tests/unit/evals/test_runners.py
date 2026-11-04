from __future__ import annotations

import json
import subprocess
import sys

import pytest

from evals import cli as review_live_cli
from evals import text_review as review_live_text
from evals.support import wait_job
from tests.support import ROOT


@pytest.mark.parametrize("passed", [False, True])
def test_live_text_review_exit_code_reflects_saved_case_results(tmp_path, monkeypatch, passed):
    monkeypatch.setenv(review_live_text.KEY_ENV, "unused-test-key")
    monkeypatch.setattr(sys, "argv", ["review", "--output", str(tmp_path)])
    monkeypatch.setattr(review_live_text, "cases", lambda: [{"name": "case"}])
    monkeypatch.setattr(review_live_text, "run_case", lambda *a: {"name": "case", "passed": passed})
    assert review_live_text.main() == int(not passed)
    assert json.loads((tmp_path / "summary.json").read_bytes())["passed"] == int(passed)


def test_cli_review_rejects_success_text_without_a_persisted_job(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_bytes(b"dummy")
    monkeypatch.setattr(
        sys, "argv", ["review", "--output", str(tmp_path), "--secret-file", str(secret)]
    )
    monkeypatch.setattr(review_live_cli, "dpapi", lambda *a, **k: b"unused-test-key")
    monkeypatch.setattr(review_live_cli, "pdf_bytes", lambda *a: b"fixture")

    def fake_run(command, **kwargs):
        if command[-1] == "doctor":
            return subprocess.CompletedProcess(command, 0, '{"ready": true}', "")
        return subprocess.CompletedProcess(command, 0, "42 [[material_fake:block_fake]]", "")

    monkeypatch.setattr(review_live_cli.subprocess, "run", fake_run)
    assert review_live_cli.main() == 1
    result = json.loads((tmp_path / "text/result.json").read_bytes())
    assert not result["passed"] and "CLI job count differs" in result["failure"]


def test_shared_live_polling_reports_http_errors_before_reading_job_shape():
    class Response:
        status_code = 503

    class Client:
        def get(self, path):
            return Response()

    with pytest.raises(AssertionError, match="poll HTTP 503"):
        wait_job(Client(), "job_test", timeout=1)


@pytest.mark.parametrize(
    "module", ["text", "text_review", "text_supplemental", "cli", "video", "video_benchmark"]
)
def test_live_evaluations_have_explicit_module_entrypoints(module):
    result = subprocess.run(
        [sys.executable, "-m", f"evals.{module}", "--help"],
        cwd=ROOT,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
