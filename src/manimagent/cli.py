from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

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
    parser = argparse.ArgumentParser(description="问题与文件到完整文字回答")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动当前文字版本")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("doctor", help="检查文字模型配置，不调用模型")
    answer_command = commands.add_parser("answer", help="生成完整 Markdown")
    answer_command.add_argument("--question", required=True)
    answer_command.add_argument("--text", help="可选补充材料")
    answer_command.add_argument("--file", type=Path, action="append", default=[])
    answer_command.add_argument("--output", type=Path)
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
            print(json.dumps({"ready": not errors, "issues": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
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
            source={"type": "inputs", "inputs": inputs, "instruction": args.question}
        )
        manager.start()
        try:
            job = manager.submit("generation", request)
            manager.queue.join()
            result = manager.store.load("jobs", job.job_id, Job)
            if result.status != "completed":
                print(result.model_dump_json(indent=2), file=sys.stderr)
                return 1
            generation = manager.store.load("generations", result.result.id, Generation)
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
