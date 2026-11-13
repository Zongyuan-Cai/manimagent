from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.schemas import Usage
from manimagent.video.renderer.process import NativeRenderer


def test_native_environment_never_contains_api_keys_or_proxy_credentials(monkeypatch):
    monkeypatch.setenv("MY_LLM_API_KEY", "private-value")
    monkeypatch.setenv("HTTPS_PROXY", "https://private:password@proxy.invalid")
    child = NativeRenderer.child_environment()
    assert "MY_LLM_API_KEY" not in child and "HTTPS_PROXY" not in child
    assert child["MPLBACKEND"] == "Agg"


def test_unavailable_memory_monitor_stops_renderer_instead_of_bypassing_limit(
    tmp_path, monkeypatch
):
    from manimagent.video.renderer import process as renderer_process

    class AccessDenied(Exception):
        pass

    class NoSuchProcess(Exception):
        pass

    def denied(pid):
        raise AccessDenied(pid)

    child = SimpleNamespace(pid=1234, poll=lambda: None)
    stopped = []
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=denied, AccessDenied=AccessDenied, NoSuchProcess=NoSuchProcess),
    )
    monkeypatch.setattr(renderer_process.subprocess, "Popen", lambda *args, **kwargs: child)
    renderer = NativeRenderer()
    monkeypatch.setattr(renderer, "stop_tree", stopped.append)
    with pytest.raises(ContentError) as exc:
        renderer._run("check", tmp_path, {}, Budget(Settings(runs_dir=tmp_path), Usage()), 30)
    assert exc.value.error.code == "render_monitor_unavailable"
    assert stopped == [child]
