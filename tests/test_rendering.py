from __future__ import annotations

import subprocess

import pytest

from manimagent.cli import SMOKE_CODE
from manimagent.config import Settings
from manimagent.rendering import DockerRenderer, validate_code


def test_valid_chinese_and_math_scene_passes_static_filter():
    validate_code(SMOKE_CODE, "RendererSmoke")


@pytest.mark.parametrize(
    "code",
    [
        "import os\nclass Demo(Scene):\n def construct(self): pass",
        "from manim import *\nclass Demo(Scene):\n def construct(self): open('x')",
        "from manim import *\nclass Demo(Scene):\n def construct(self): self.__class__",
        "from manim import *\nclass Demo(Scene):\n def construct(self): config.frame_rate=1",
        "from manim import *\nclass Wrong(Scene):\n def construct(self): pass",
        "from manim import *\nclass Demo: \n def construct(self): pass",
        "bad syntax !",
    ],
)
def test_unsafe_or_malformed_code_rejected(code):
    with pytest.raises((SyntaxError, ValueError)):
        validate_code(code, "Demo")


def test_timeout_removes_only_the_named_container(monkeypatch, tmp_path):
    calls = []

    def process(args, timeout):
        calls.append(args)
        if args[1] == "run":
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("manimagent.rendering._process", process)
    renderer = DockerRenderer(Settings())
    result = renderer.render(SMOKE_CODE, "RendererSmoke", tmp_path / "work", False, 1)
    assert result.error_category == "timeout"
    run = calls[0]
    assert "--network=none" in run and "--read-only" in run and "--cap-drop=ALL" in run
    name = run[run.index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "-f", name]
    assert not renderer._active


def test_container_output_cannot_escape_mount(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "private.txt").write_text("private")
    with pytest.raises(ValueError):
        DockerRenderer._safe_output(work, "../private.txt")


def test_closed_renderer_never_starts_new_container(monkeypatch, tmp_path):
    renderer = DockerRenderer(Settings())
    renderer.close()
    with pytest.raises(InterruptedError):
        renderer.render(SMOKE_CODE, "RendererSmoke", tmp_path, True, 1)
