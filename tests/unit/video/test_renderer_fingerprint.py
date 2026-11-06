"""Evidence fingerprints follow renderer dependencies and survive installation moves."""

import ast
import importlib.util
import shutil
from pathlib import Path

import pytest

from manimagent.video.renderer import process


def test_renderer_fingerprint_covers_transitive_local_imports(monkeypatch):
    root = Path(process.manimagent.__file__).resolve().parent
    hashed = set()
    read_bytes = Path.read_bytes

    def record_read(path):
        hashed.add(path.resolve())
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    process.renderer_fingerprint()
    required, pending = (
        set(),
        ["manimagent.video.renderer.process", "manimagent.video.renderer.worker"],
    )
    while pending:
        module = pending.pop()
        relative = module.split(".")[1:]
        path = root.joinpath(*relative).with_suffix(".py") if relative else root / "__init__.py"
        if not path.is_file():
            path = root.joinpath(*relative, "__init__.py")
        if path in required:
            continue
        required.add(path)
        # Importing a submodule also executes each parent package initializer.
        pending.extend(".".join(module.split(".")[:i]) for i in range(1, len(module.split("."))))
        package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [
                    importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                ]
            pending.extend(
                name for name in imports if name == "manimagent" or name.startswith("manimagent.")
            )
    assert required <= hashed, sorted(str(path.relative_to(root)) for path in required - hashed)


@pytest.fixture
def renderer_tree(tmp_path, monkeypatch):
    root = tmp_path / "manimagent"
    paths = [
        "__init__.py",
        "budget.py",
        "config.py",
        "errors.py",
        "schemas.py",
        "memory/__init__.py",
        "memory/models.py",
        "video/__init__.py",
        "video/models.py",
        "video/notation.py",
        "video/renderer/__init__.py",
        "video/renderer/process.py",
        "video/renderer/worker.py",
        "video/renderer/layout.py",
        "video/renderer/scene.py",
        "video/renderer/media.py",
    ]
    for name in paths:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"same source bytes\n")
    monkeypatch.setattr(process.manimagent, "__file__", str(root / "__init__.py"))
    return root


@pytest.mark.parametrize(
    "relative",
    ["video/renderer/layout.py", "video/models.py", "video/notation.py", "memory/models.py"],
)
def test_geometry_and_contract_changes_invalidate_renderer_fingerprint(renderer_tree, relative):
    before = process.renderer_fingerprint()
    (renderer_tree / relative).write_bytes(b"changed source bytes\n")
    assert process.renderer_fingerprint() != before


def test_renderer_fingerprint_binds_relative_paths_and_additional_modules(renderer_tree):
    before = process.renderer_fingerprint()
    source = renderer_tree / "video/renderer/layout.py"
    source.rename(source.with_name("geometry.py"))
    # Every file has identical bytes: this detects paths being omitted from the hash.
    renamed = process.renderer_fingerprint()
    assert renamed != before
    source.write_bytes(b"same source bytes\n")
    assert process.renderer_fingerprint() not in {before, renamed}


def test_renderer_fingerprint_is_independent_of_installation_path(
    renderer_tree, tmp_path, monkeypatch
):
    before = process.renderer_fingerprint()
    installed = tmp_path / "installed" / "manimagent"
    shutil.copytree(renderer_tree, installed)
    monkeypatch.setattr(process.manimagent, "__file__", str(installed / "__init__.py"))
    assert process.renderer_fingerprint() == before
