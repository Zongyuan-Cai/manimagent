"""JSON-only child-process entry point for the trusted native renderer."""

from __future__ import annotations

import argparse
import json
import subprocess
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["render", "assemble", "check", "layout"])
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    directory = args.input.resolve().parent
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.action == "check":
            import av
            import imageio_ffmpeg
            import manim

            from manimagent.video.renderer.layout import font_name

            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-version"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            result = {
                "status": "success",
                "font": font_name(data.get("font", "auto")),
                "manim": manim.__version__,
                "av": av.__version__,
            }
        elif args.action == "assemble":
            from manimagent.video.renderer.media import assemble

            result = assemble(data, directory)
        else:
            from manimagent.video.renderer.scene import render

            if args.action == "layout":
                data["layout_only"] = True
            result = render(data, directory)
    except Exception as exc:
        (directory / "error.txt").write_text(traceback.format_exc()[-12000:], encoding="utf-8")
        result = {"status": "error", "message": str(exc)[:1000]}
    (directory / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
