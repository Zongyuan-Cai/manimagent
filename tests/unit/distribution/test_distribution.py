import pytest

from scripts.check_distribution import REQUIRED_ASSETS, check_members, check_source_contents


@pytest.mark.parametrize("sdist", [False, True])
@pytest.mark.parametrize("asset", ["vendor/tex-svg.js", "memory.js"])
def test_current_web_assets_are_required_in_both_distribution_formats(sdist, asset):
    prefix = "manimagent-0.3.1/src/" if sdist else ""
    members = [prefix + name for name in REQUIRED_ASSETS]
    check_members(members, sdist=sdist)
    members.remove(prefix + "manimagent/web/" + asset)
    with pytest.raises(ValueError, match="Missing packaged web assets"):
        check_members(members, sdist=sdist)


@pytest.mark.parametrize(
    "member",
    [
        "legacy_mvp/src/manimagent/workflow.py",
        "runs/current/private/configuration/credentials.json",
        ".env",
        ".env.production",
        "config.yaml",
        "testmaterial/sample.txt",
        "testresult/answer.md",
        "../outside.txt",
        "/absolute.txt",
        "runs\\private\\credentials.json",
    ],
)
def test_distribution_rejects_private_historical_and_unsafe_paths(member):
    with pytest.raises(ValueError, match="content in distribution|Unsafe archive path"):
        check_members([*REQUIRED_ASSETS, member], sdist=False)


@pytest.mark.parametrize("sdist", [False, True])
def test_distribution_verifies_all_runtime_modules_against_current_source(tmp_path, sdist):
    (tmp_path / "__init__.py").write_bytes(b"current")
    web = tmp_path / "web"
    web.mkdir()
    (web / "new-module.js").write_bytes(b"new module")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "local.pyc").write_bytes(b"local bytecode")
    prefix = "manimagent-0.3.1/src/manimagent/" if sdist else "manimagent/"
    contents = {prefix + "__init__.py": b"current", prefix + "web/new-module.js": b"new module"}
    assert check_source_contents(contents, tmp_path, sdist=sdist) == 2
    with pytest.raises(ValueError, match="Missing runtime files"):
        check_source_contents({prefix + "__init__.py": b"current"}, tmp_path, sdist=sdist)
    with pytest.raises(ValueError, match="differs from current source"):
        check_source_contents({**contents, prefix + "__init__.py": b"old"}, tmp_path, sdist=sdist)
    with pytest.raises(ValueError, match="Unexpected runtime files"):
        check_source_contents({**contents, prefix + "retired.py": b"dead"}, tmp_path, sdist=sdist)
