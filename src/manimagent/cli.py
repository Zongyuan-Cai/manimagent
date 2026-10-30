from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pydantic import ValidationError

from manimagent.animation_models import VideoRequest, VideoResult
from manimagent.api import create_app
from manimagent.config import load_settings
from manimagent.content_store import ContentError
from manimagent.schemas import Answer, Generation, Job, TextGenerationRequest
from manimagent.text_jobs import TextJobManager


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="问题与文件到完整解答与无声教学动画")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动文字与无声动画服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    doctor = commands.add_parser("doctor", help="检查模型配置，不调用模型")
    doctor.add_argument("--animation", action="store_true", help="同时检查本地渲染依赖")
    for name, help_text in [
        ("answer", "生成完整 Markdown"),
        ("lesson", "生成完整文字与无声教学视频"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--question", required=True)
        command.add_argument("--text", help="可选补充材料")
        command.add_argument("--file", type=Path, action="append", default=[])
        command.add_argument("--output", type=Path)
    video_command = commands.add_parser("video", help="使用已保存的回答生成动画，不重新回答")
    video_command.add_argument("--answer-id", required=True)
    video_command.add_argument("--resume-job-id")
    video_command.add_argument("--output", type=Path, default=Path("teaching.mp4"))
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command == "serve":
            import uvicorn

            uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
            return 0
        manager = TextJobManager(settings)
        if args.command == "doctor":
            errors = manager.readiness()
            if args.animation:
                errors += manager.renderer.preflight()
            print(json.dumps({"ready": not errors, "issues": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
        if args.command == "video":
            request = VideoRequest(answer_id=args.answer_id, resume_job_id=args.resume_job_id)
        else:
            inputs = [{"type": "text", "text": args.question}]
            if args.text is not None:
                inputs.append({"type": "text", "text": args.text})
            TextGenerationRequest(
                source={"type": "inputs", "inputs": inputs, "instruction": args.question}
            )
            for path in args.file:
                with path.open("rb") as stream:
                    asset = manager.store.put_file(stream, path.name)
                inputs.append({"type": "file", "file_id": asset.file_id})
            request = TextGenerationRequest(
                output_mode="both" if args.command == "lesson" else "text",
                source={"type": "inputs", "inputs": inputs, "instruction": args.question},
            )
        manager.start()
        try:
            job = manager.submit("video" if args.command == "video" else "generation", request)
            manager.queue.join()
            result = manager.store.load("jobs", job.job_id, Job)
            generation = None
            if result.resource_ref and result.resource_ref.type == "generation":
                generation = manager.store.load("generations", result.resource_ref.id, Generation)
            if args.command == "lesson" and generation and generation.answer_id:
                text = manager.store.load("answers", generation.answer_id, Answer)
                (args.output or Path("teaching.mp4")).with_suffix(".md").write_text(
                    text.markdown, encoding="utf-8"
                )
            if result.status != "completed":
                print(result.model_dump_json(indent=2), file=sys.stderr)
                return 1
            if args.command in {"lesson", "video"}:
                video_id = generation.video_id if generation else result.result.id
                video = manager.store.load("videos", video_id, VideoResult)
                output = args.output or Path("teaching.mp4")
                shutil.copyfile(manager.store.file_path(video.video_asset_id), output)
                print(video.model_dump_json(indent=2))
                return 0
            answer = manager.store.load("answers", generation.answer_id, Answer)
            if args.output:
                args.output.write_text(answer.markdown, encoding="utf-8", newline="")
            else:
                sys.stdout.write(answer.markdown)
            return 0
        finally:
            manager.stop()
    except (ValidationError, ValueError, OSError, ContentError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
