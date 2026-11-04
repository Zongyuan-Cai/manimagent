from evals.support import source_hash


def test_benchmark_detects_nested_changes_and_renamed_sources(tmp_path):
    for name in (
        "uv.lock",
        "pyproject.toml",
        "src/manimagent/video/renderer/scene.py",
        "evals/cases/video.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    original = source_hash(tmp_path)
    source = tmp_path / "src/manimagent/video/renderer/scene.py"
    source.write_text("changed", encoding="utf-8")
    assert source_hash(tmp_path) != original
    source.write_text("original", encoding="utf-8")
    assert source_hash(tmp_path) == original
    source.rename(source.with_name("layout.py"))
    renamed = source_hash(tmp_path)
    assert renamed != original
    (tmp_path / "evals/cases/video.py").write_text("new topic", encoding="utf-8")
    assert source_hash(tmp_path) != renamed
