from __future__ import annotations

import json
import threading

import pytest
from pydantic import ValidationError

from manimagent.budget import Budget
from manimagent.cli import main
from manimagent.config import ModelConfig, Settings, load_settings
from manimagent.schemas import Usage
from tests.helpers import make_engine


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "https:///v1",
    ],
)
def test_model_url_rejects_credentials_and_non_http(url):
    with pytest.raises(ValidationError):
        ModelConfig(base_url=url)


def test_env_and_relative_config_are_loaded_from_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANIMAGENT_CONFIG", raising=False)
    monkeypatch.delenv("CONFIG_TEST_URL", raising=False)
    (tmp_path / ".env").write_text("CONFIG_TEST_URL=https://example.com/v1\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "runs_dir: output\nmodels:\n  text:\n    base_url: ${CONFIG_TEST_URL}\n    model: test\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.model_for("coder").base_url == "https://example.com/v1"
    assert settings.runs_dir == tmp_path / "output"
    monkeypatch.delenv("CONFIG_TEST_URL")


def test_malformed_yaml_has_actionable_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("models: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_settings(path)


def test_missing_environment_placeholders_mean_unconfigured_not_invalid_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_UNSET_MODEL", raising=False)
    monkeypatch.delenv("MVP_UNSET_URL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        "models:\n  text:\n    model: ${MVP_UNSET_MODEL}\n    base_url: ${MVP_UNSET_URL}\n",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.model_for("coder").model == ""
    assert settings.configuration_errors()
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="对象"):
        load_settings(path)


def test_cli_generation_uses_shared_pipeline(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path)
    monkeypatch.setattr("manimagent.cli.build_engine", lambda settings: engine)
    assert main(["generate", "--text", "解释向量"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_cli_validates_text_before_checking_services(monkeypatch, capsys):
    def unexpected(settings):
        raise AssertionError("Invalid text must not initialize runtime")

    monkeypatch.setattr("manimagent.cli.build_engine", unexpected)
    assert main(["generate", "--text", "   "]) == 2
    assert "3000" in capsys.readouterr().err


def test_shutdown_cancels_before_new_model_call():
    stop = threading.Event()
    budget = Budget(Settings(), Usage(), stop)
    stop.set()
    with pytest.raises(InterruptedError):
        budget.before_call()
    assert budget.usage.model_calls == 0
