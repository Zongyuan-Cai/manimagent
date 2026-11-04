from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize("exit_code", [0, 1, -1])
def test_live_benchmark_requires_successful_process_exit(tmp_path, monkeypatch, exit_code):
    from pathlib import Path
    from types import SimpleNamespace

    from evals import video_benchmark as runner

    def process(command, **kwargs):
        directory = Path(command[command.index("--output") + 1])
        runner.save(directory / "result.json", {"passed": True})
        return SimpleNamespace(wait=lambda timeout: exit_code)

    monkeypatch.setenv(runner.KEY_ENV, "unused-controlled-credential")
    monkeypatch.setattr(runner, "source_hash", lambda root: "fixed")
    monkeypatch.setattr(runner.subprocess, "Popen", process)
    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["benchmark", "--output", str(tmp_path), "--topics", "projection", "--repeats", "1"],
    )
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == (0 if exit_code == 0 else 1)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == (1 if exit_code == 0 else 0)
