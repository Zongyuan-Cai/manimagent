import ast
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


def imported_modules(path, root):
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            package = list(path.relative_to(root).parts[:-1])
            prefix = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*prefix, node.module or ""]).rstrip(".")
            modules.extend([module, *(f"{module}.{alias.name}" for alias in node.names)])
    return modules


def test_capabilities_do_not_depend_on_application_entrypoints():
    source = ROOT / "src/manimagent"
    for capability in ("llm", "materials", "memory", "answers", "video"):
        forbidden = ("manimagent.api", "manimagent.jobs", "manimagent.cli")
        if capability == "llm":
            forbidden += ("manimagent.materials", "manimagent.answers", "manimagent.video")
        for path in (source / capability).rglob("*.py"):
            modules = imported_modules(path, source.parent)
            assert not any(
                module == entry or module.startswith(entry + ".")
                for module in modules
                for entry in forbidden
            ), f"{path.relative_to(source)} imports an application entrypoint: {modules}"


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


def test_shared_test_helpers_do_not_import_test_case_modules():
    for path in (ROOT / "tests").rglob("*.py"):
        assert not any(
            module == f"tests.{suite}" or module.startswith(f"tests.{suite}.")
            for module in imported_modules(path, ROOT)
            for suite in ("unit", "integration", "native")
        ), f"{path.relative_to(ROOT)} imports a test case; move shared helpers to tests/support"


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("from ..unit.llm.test_client import provider", "tests.unit.llm.test_client"),
        ("from .. import unit", "tests.unit"),
        ("from tests.unit import test_architecture", "tests.unit.test_architecture"),
    ],
)
def test_architecture_checks_resolve_relative_and_absolute_imports(tmp_path, statement, expected):
    path = tmp_path / "tests/support/helpers.py"
    path.parent.mkdir(parents=True)
    path.write_text(statement, encoding="utf-8")
    assert expected in imported_modules(path, tmp_path)


def test_model_configuration_does_not_load_material_or_content_storage_modules():
    script = (
        "import sys,json;import manimagent.llm.settings;"
        "print(json.dumps([m for m in sys.modules "
        "if m == 'manimagent.content_store' or m.startswith('manimagent.materials')]))"
    )
    assert json.loads(subprocess.check_output([sys.executable, "-c", script], text=True)) == []


def test_current_config_rejects_legacy_renderer_field_and_resolves_its_own_data_root(
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
