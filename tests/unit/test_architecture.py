import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_current_application_does_not_import_or_expose_historical_mvp(tmp_path):
    script = "import sys,json;from manimagent.api import create_app;print(json.dumps([m for m in sys.modules if m.startswith(('langgraph','manimagent.rendering','manimagent.workflow','manimagent.agents'))]))"
    assert json.loads(subprocess.check_output([sys.executable, "-c", script], text=True)) == []
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        assert client.get("/api/v2/health").status_code == 200
        assert client.post("/api/jobs", json={"text": "old input"}).status_code == 404
        assert not any("/api/jobs" in path for path in client.get("/openapi.json").json()["paths"])
    help_text = subprocess.check_output(
        [sys.executable, "-m", "manimagent", "--help"], text=True, encoding="utf-8"
    )
    assert "generate" not in help_text and "smoke" not in help_text


def test_legacy_project_has_its_own_source_dependencies_and_tests():
    legacy = ROOT / "legacy_mvp"
    if not legacy.exists():
        pytest.skip(
            "Repository-level archive check; the current sdist deliberately excludes legacy"
        )
    assert all(
        (legacy / name).exists()
        for name in [
            "pyproject.toml",
            "uv.lock",
            "src/manimagent/workflow.py",
            "tests/test_workflow.py",
        ]
    )
    assert not (ROOT / "src/manimagent/workflow.py").exists()
    assert "langgraph" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_current_config_rejects_animation_fields_and_resolves_its_own_data_root(
    tmp_path, monkeypatch
):
    with pytest.raises(ValidationError):
        Settings(renderer_image="historical")
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("runs_dir: current-output\nmodels: {}\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.runs_dir == tmp_path / "current-output"
    assert settings.private_dir == tmp_path / "current-output/private"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:secret@example.com",
        "https://example.com?key=secret",
        "https:///v1",
    ],
)
def test_configuration_rejects_credential_urls(url):
    with pytest.raises(ValidationError):
        ModelConfig(base_url=url)
