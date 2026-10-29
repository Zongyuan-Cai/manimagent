import pytest

from scripts.check_distribution import REQUIRED_ASSETS, check_members


@pytest.mark.parametrize("sdist", [False, True])
def test_current_web_assets_are_required_in_both_distribution_formats(sdist):
    prefix = "manimagent-0.2.0/src/" if sdist else ""
    members = [prefix + name for name in REQUIRED_ASSETS]
    check_members(members, sdist=sdist)
    members.remove(prefix + "manimagent/web/vendor/tex-svg.js")
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
