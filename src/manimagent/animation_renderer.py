"""A bounded child process runs the repository's renderer, never generated code."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from manimagent.animation_models import PlannedScene, digest
from manimagent.budget import Budget, BudgetExceeded
from manimagent.content_store import ContentError


class NativeRenderer:
    @staticmethod
    def preflight() -> list[str]:
        missing = [
            name
            for name in ["manim", "matplotlib", "av", "imageio_ffmpeg", "psutil"]
            if importlib.util.find_spec(name) is None
        ]
        return (
            ["请安装本地动画依赖：uv sync --extra animation（缺少 " + ", ".join(missing) + "）"]
            if missing
            else []
        )

    @staticmethod
    def child_environment() -> dict[str, str]:
        # Deliberate allowlist: providers, proxy credentials and .env variables never cross.
        allowed = {
            "systemroot",
            "windir",
            "path",
            "temp",
            "tmp",
            "userprofile",
            "appdata",
            "localappdata",
            "systemdrive",
            "programdata",
            "lang",
            "lc_all",
        }
        result = {k: v for k, v in os.environ.items() if k.lower() in allowed}
        result.update(
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            PYTHONUTF8="1",
            MPLBACKEND="Agg",
            MPLCONFIGDIR="matplotlib-cache",
        )
        return result

    @staticmethod
    def stop_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            import psutil

            try:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in children:
                    child.kill()
            except psutil.NoSuchProcess:
                pass
            process.kill()
        process.wait(timeout=5)

    def run(self, action: str, directory: Path, payload: dict, budget: Budget, cap: float) -> dict:
        import psutil

        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "input.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        timeout = min(cap, budget.remaining() - 15)
        if timeout <= 0:
            raise BudgetExceeded("剩余时间不足以渲染并保存结果")
        started = time.monotonic()
        peak = 0
        with (directory / "worker.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "manimagent.animation_driver",
                    action,
                    "--input",
                    str(source.resolve()),
                ],
                cwd=directory,
                env=self.child_environment(),
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                while process.poll() is None:
                    budget.remaining()
                    if time.monotonic() - started >= timeout:
                        raise ContentError("render_timeout", "本地渲染达到时间上限")
                    try:
                        parent = psutil.Process(process.pid)
                        usage = sum(
                            p.memory_info().rss
                            for p in [parent, *parent.children(recursive=True)]
                            if p.is_running()
                        )
                        peak = max(peak, usage)
                        if usage > budget.settings.video_memory_mb * 1024 * 1024:
                            raise ContentError("render_resource_limit", "本地渲染超过内存上限")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    time.sleep(0.1)
            finally:
                self.stop_tree(process)
        result_file = directory / "result.json"
        if process.returncode or not result_file.is_file():
            raise ContentError("renderer_unavailable", "本地渲染器执行失败，请查看渲染日志")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("status") != "success":
            message = result.get("message", "渲染失败")
            raise ContentError(
                "render_layout"
                if "layout:" in message
                else "render_formula"
                if "Parse" in message or "Unknown symbol" in message
                else "render_failed",
                message,
                state="needs_review",
            )
        if action not in {"check", "layout"}:
            video = (directory / result["video_path"]).resolve()
            if not video.is_relative_to(directory.resolve()) or not video.is_file():
                raise ContentError("invalid_render", "渲染结果路径无效")
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        result["peak_memory_mb"] = round(peak / 1024 / 1024, 2)
        result["renderer_hash"] = digest(
            Path(__file__).with_name("animation_driver.py").read_text(encoding="utf-8")
        )
        return result

    def validate_runtime(self, directory: Path, budget: Budget) -> dict:
        return self.run("check", directory, {"font": budget.settings.video_font}, budget, 30)

    def validate_layout(self, scene, directory, outline, scene_count, budget, index):
        return self.run(
            "layout",
            directory,
            {
                "scene": scene.model_dump(),
                "title": outline.title,
                "scene_index": index,
                "scene_count": scene_count,
                "font": budget.settings.video_font,
            },
            budget,
            30,
        )

    def render(
        self, scene: PlannedScene, directory: Path, plan, budget: Budget, index: int
    ) -> dict:
        result = self.run(
            "render",
            directory,
            {
                "scene": scene.model_dump(),
                "title": plan.outline.title,
                "scene_index": index,
                "scene_count": len(plan.scenes),
                "font": budget.settings.video_font,
            },
            budget,
            budget.settings.video_render_timeout,
        )
        media = result["media"]
        if abs(media["duration_seconds"] - scene.duration_seconds) > max(
            2, scene.duration_seconds * 0.05
        ):
            raise ContentError("render_timing", "场景实测时长与计划不符", state="needs_review")
        if [e["beat_id"] for e in result["events"]] != [b.beat_id for b in scene.beats]:
            raise ContentError("render_coverage", "渲染缺少教学节拍", state="needs_review")
        for event, beat in zip(result["events"], scene.beats, strict=True):
            stable = event["stable_seconds"]
            if (
                abs(stable - beat.stable_seconds) > 0.15
                or abs(event["end_seconds"] - beat.end_seconds) > 0.15
            ):
                raise ContentError("render_timing", "节拍阅读区间与计划不符", state="needs_review")
            if event["end_seconds"] - stable < 5 or event["objects"] != [
                o.object_id for o in beat.diagram.objects
            ]:
                raise ContentError(
                    "render_coverage", "图形或稳定阅读时间不完整", state="needs_review"
                )
        return result

    def assemble(self, sources: list[Path], directory: Path, plan, budget: Budget) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        names = []
        for i, source in enumerate(sources):
            name = f"scene_{i:02d}.mp4"
            shutil.copyfile(source, directory / name)
            names.append(name)
        result = self.run("assemble", directory, {"videos": names}, budget, 60)
        duration = result["media"]["duration_seconds"]
        if not 120 <= duration <= 360 or abs(duration - plan.duration_seconds) > max(
            3, plan.duration_seconds * 0.05
        ):
            raise ContentError(
                "render_timing", "成片时长不符合 120～360 秒及计划偏差限制", state="needs_review"
            )
        return result
