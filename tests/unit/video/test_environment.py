from __future__ import annotations

from manimagent.video.renderer.process import NativeRenderer


def test_native_environment_never_contains_api_keys_or_proxy_credentials(monkeypatch):
    monkeypatch.setenv("MY_LLM_API_KEY", "private-value")
    monkeypatch.setenv("HTTPS_PROXY", "https://private:password@proxy.invalid")
    child = NativeRenderer.child_environment()
    assert "MY_LLM_API_KEY" not in child and "HTTPS_PROXY" not in child
    assert child["MPLBACKEND"] == "Agg"
