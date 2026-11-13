from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

import pytest

from scripts import launch_local
from tests.support import ROOT


def test_socket_permission_failure_is_not_reported_as_busy_ports(monkeypatch):
    attempts = []
    stopped = []

    class DeniedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def bind(self, address):
            attempts.append(address)
            raise PermissionError(errno.EACCES, "permission denied")

    manager = SimpleNamespace(start=lambda: None, stop=lambda: stopped.append(True))
    monkeypatch.setattr(
        launch_local,
        "create_app",
        lambda: SimpleNamespace(state=SimpleNamespace(job_manager=manager)),
    )
    monkeypatch.setattr(launch_local.os, "chdir", lambda _path: None)
    monkeypatch.setattr(launch_local.socket, "socket", lambda: DeniedSocket())
    with pytest.raises(PermissionError):
        launch_local.main()
    assert len(attempts) == 1 and stopped == [True]


def test_desktop_second_launch_reuses_the_real_http_service(tmp_path):
    root = ROOT
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "runs_dir: " + (tmp_path / "runtime").as_posix() + "\nmodels: {}\n", encoding="utf-8"
    )
    # Suppress opening an interactive browser; service startup/HTTP/OS locking are real.
    script = "import runpy,webbrowser;webbrowser.open=lambda url: print('OPEN '+url,flush=True);runpy.run_path('scripts/launch_local.py',run_name='__main__')"
    env = {**os.environ, "MANIMAGENT_CONFIG": str(cfg), "PYTHONIOENCODING": "utf-8"}
    with (tmp_path / "launcher.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script], cwd=root, env=env, stdout=log, stderr=log
        )
        try:
            metadata = tmp_path / "runtime/private/instance.json"
            deadline = time.monotonic() + 15
            owner = None
            while time.monotonic() < deadline:
                try:
                    candidate = json.loads(metadata.read_text(encoding="utf-8"))
                    if candidate.get("url"):
                        with build_opener(ProxyHandler({})).open(
                            candidate["url"] + "/api/v2/health", timeout=1
                        ) as response:
                            if json.load(response)["instance_id"] == candidate["id"]:
                                owner = candidate
                                break
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            assert owner, "Launcher failed to expose its owned HTTP service"
            second = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            assert second.returncode == 0, second.stderr
            assert "OPEN " + owner["url"] in second.stdout
            assert json.loads(metadata.read_text(encoding="utf-8"))["id"] == owner["id"]
            assert process.poll() is None
        finally:
            process.terminate()
            process.wait(timeout=10)
