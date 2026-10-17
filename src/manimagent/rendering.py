from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Protocol

from manimagent.config import Settings
from manimagent.schemas import RenderResult


class Renderer(Protocol):
    def preflight(self) -> list[str]: ...
    def render(
        self, code: str, class_name: str, directory: Path, formal: bool, timeout: float
    ) -> RenderResult: ...
    def assemble(self, videos: list[Path], directory: Path, timeout: float) -> RenderResult: ...


def validate_code(code: str, class_name: str) -> None:
    """A preflight filter, never a substitute for the container boundary."""
    tree = ast.parse(code)
    allowed_imports = {"manim", "numpy", "math"}
    blocked = {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "input",
        "breakpoint",
        "exit",
        "quit",
    }
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name]
    if len(classes) != 1 or not any(
        isinstance(n, ast.FunctionDef) and n.name == "construct" for n in classes[0].body
    ):
        raise ValueError("缺少指定 Scene 类或 construct 方法")
    if not any(isinstance(base, ast.Name) and base.id == "Scene" for base in classes[0].bases):
        raise ValueError("场景必须直接继承 Scene")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in allowed_imports for alias in node.names):
                raise ValueError("只允许 manim、numpy、math 导入")
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed_imports:
                raise ValueError("不允许此模块导入")
        if isinstance(node, ast.Name) and (node.id in blocked or node.id.startswith("__")):
            raise ValueError("代码包含禁止的动态或系统访问")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("_")
            or isinstance(node.value, ast.Name)
            and node.value.id == "config"
        ):
            raise ValueError("不允许私有属性或全局 config 访问")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("不允许全局状态修改")


def _process(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(0.1, timeout),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class DockerRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = Path(__file__).with_name("renderer_driver.py").resolve()
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._closed = False

    def preflight(self) -> list[str]:
        if not shutil.which("docker"):
            return ["未找到 Docker，请安装并启动 Docker Desktop 或 Docker Engine"]
        try:
            result = _process(["docker", "image", "inspect", self.settings.renderer_image], 10)
            if result.returncode:
                return ["Docker 未就绪或渲染镜像不存在，请启动 Docker 并运行镜像构建命令"]
        except (OSError, subprocess.TimeoutExpired):
            return ["无法连接 Docker Engine"]
        return []

    def _run(self, directory: Path, args: list[str], timeout: float) -> RenderResult:
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        name = "manimagent-" + uuid.uuid4().hex
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--memory=4g",
            "--cpus=2",
            "--user",
            user,
            "--tmpfs",
            "/tmp:rw,nosuid,size=536870912",
            "-e",
            "HOME=/tmp",
            "-e",
            "XDG_CACHE_HOME=/tmp/cache",
            "-e",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "-e",
            "TEXMFVAR=/tmp/texmf-var",
            "-e",
            "TEXMFCONFIG=/tmp/texmf-config",
            "-v",
            f"{directory}:/work",
            "-v",
            f"{self.driver}:/runner/driver.py:ro",
            "-w",
            "/work",
            "--entrypoint",
            "python",
            self.settings.renderer_image,
            "/runner/driver.py",
            *args,
        ]
        with self._lock:
            if self._closed:
                raise InterruptedError("渲染器已停止")
            self._active.add(name)
        try:
            completed = _process(cmd, timeout)
            (directory / "container.log").write_text(
                (completed.stdout + "\n" + completed.stderr)[-60000:], encoding="utf-8"
            )
            result_file = directory / "result.json"
            if completed.returncode or not result_file.is_file():
                return RenderResult(
                    status="error",
                    error_category="environment",
                    message="渲染容器执行失败，请检查容器日志与镜像",
                )
            data = json.loads(result_file.read_text(encoding="utf-8"))
            data.pop("metadata", None)
            result = RenderResult.model_validate(data)
            if result.status == "success":
                result.video_path = str(self._safe_output(directory, result.video_path or ""))
                result.frames = [
                    str(self._safe_output(directory, frame)) for frame in result.frames
                ]
                if len(result.frames) != 4 or len(result.timestamps) != 4:
                    raise ValueError("抽帧数量不完整")
            return result
        except subprocess.TimeoutExpired:
            return RenderResult(
                status="error", error_category="timeout", message="渲染达到时间上限"
            )
        except (OSError, ValueError, KeyError):
            return RenderResult(
                status="error", error_category="environment", message="渲染产物无法校验"
            )
        finally:
            with self._lock:
                self._active.discard(name)
            try:
                _process(["docker", "rm", "-f", name], 10)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            names = list(self._active)
        for name in names:
            try:
                _process(["docker", "rm", "-f", name], 10)
            except (OSError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _safe_output(directory: Path, value: str) -> Path:
        path = (directory / value).resolve(strict=True)
        if not path.is_relative_to(directory) or not path.is_file() or not path.stat().st_size:
            raise ValueError("容器产物路径无效")
        return path

    def render(
        self, code: str, class_name: str, directory: Path, formal: bool, timeout: float
    ) -> RenderResult:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scene.py").write_text(code, encoding="utf-8")
        try:
            validate_code(code, class_name)
        except (SyntaxError, ValueError) as exc:
            return RenderResult(status="error", error_category="static", message=str(exc))
        return self._run(directory, ["render", class_name, "m" if formal else "l"], timeout)

    def assemble(self, videos: list[Path], directory: Path, timeout: float) -> RenderResult:
        directory.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(videos):
            shutil.copyfile(path, directory / f"scene_{index:02d}.mp4")
        return self._run(directory, ["assemble"], timeout)
