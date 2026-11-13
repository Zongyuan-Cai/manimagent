import pytest

from manimagent.config import load_settings


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_explicit_missing_config_is_an_error(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "missing.yaml"
    if source == "environment":
        monkeypatch.setenv("MANIMAGENT_CONFIG", str(path))
    with pytest.raises(FileNotFoundError):
        load_settings(path if source == "argument" else None)


def test_default_config_can_be_absent_for_first_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANIMAGENT_CONFIG", raising=False)
    assert load_settings().runs_dir == tmp_path / "runs/current"


@pytest.mark.parametrize("value", ["", "null", "false", "0", "[]", '""'])
def test_config_must_be_an_object_even_when_value_is_falsy(tmp_path, value):
    path = tmp_path / "invalid.yaml"
    path.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="YAML 对象"):
        load_settings(path)


def test_missing_environment_substitution_is_not_an_empty_setting(tmp_path, monkeypatch):
    name = "MANIMAGENT_TEST_MISSING_VARIABLE"
    monkeypatch.delenv(name, raising=False)
    path = tmp_path / "settings.yaml"
    path.write_text(f"runs_dir: ${{{name}}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=name):
        load_settings(path)
    monkeypatch.setenv(name, "output")
    assert load_settings(path).runs_dir == tmp_path / "output"
