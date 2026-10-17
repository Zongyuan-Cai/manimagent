from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from manimagent.api import build_engine, create_app
from manimagent.config import load_settings
from manimagent.rendering import DockerRenderer
from manimagent.schemas import GenerationRequest

SMOKE_CODE = """from manim import *
class RendererSmoke(Scene):
    def construct(self):
        self.camera.background_color = "#101827"
        title = Text("二维向量投影", font="Noto Sans CJK SC", font_size=36).to_edge(UP)
        plane = NumberPlane(x_range=[-1, 5, 1], y_range=[-1, 4, 1], x_length=8, y_length=5)
        a = Arrow(plane.c2p(0, 0), plane.c2p(3, 2), buff=0, color=BLUE)
        projection = Arrow(plane.c2p(0, 0), plane.c2p(3, 0), buff=0, color=YELLOW)
        foot = DashedLine(plane.c2p(3, 2), plane.c2p(3, 0), color=WHITE)
        formula = MathTex(r"\\operatorname{proj}_{b}(a)=(3,0)").scale(0.75).to_edge(DOWN)
        self.play(Create(plane), Write(title), run_time=1)
        self.play(GrowArrow(a), run_time=1)
        self.play(Create(foot), GrowArrow(projection), run_time=1)
        self.play(Write(formula), run_time=1)
        self.wait(1)
"""


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="纯文本到 Manim 动画")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动本地网页")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    generate = commands.add_parser("generate", help="生成一次动画")
    inputs = generate.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text")
    inputs.add_argument("--stdin", action="store_true")
    commands.add_parser("doctor", help="检查配置与渲染环境，不调用模型")
    commands.add_parser("smoke", help="运行手写场景的真实渲染检查，不调用模型")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command == "serve":
            import uvicorn

            uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
            return 0
        if args.command == "smoke":
            renderer = DockerRenderer(settings)
            errors = renderer.preflight()
            if errors:
                print(json.dumps({"errors": errors}, ensure_ascii=False))
                return 1
            directory = settings.runs_dir / "_smoke" / uuid.uuid4().hex
            result = renderer.render(SMOKE_CODE, "RendererSmoke", directory / "render", True, 180)
            if result.status == "success":
                result = renderer.assemble([Path(result.video_path)], directory / "assembly", 180)
            print(result.model_dump_json(indent=2))
            return 0 if result.status == "success" else 1
        request = None
        if args.command == "generate":
            request = GenerationRequest(text=sys.stdin.read() if args.stdin else args.text)
        engine = build_engine(settings)
        errors = engine.readiness()
        if args.command == "doctor":
            print(json.dumps({"ready": not errors, "issues": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
        if errors:
            print("；".join(errors), file=sys.stderr)
            return 1
        manifest = engine.store.create(request)
        try:
            result = engine.execute(manifest.job_id)
        finally:
            engine.stop()
        print(json.dumps(engine.store.public(result), ensure_ascii=False, indent=2))
        return 0 if result.status == "completed" else 1
    except (ValidationError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
