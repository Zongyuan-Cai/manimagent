"""Standalone container driver. No host package dependencies beyond the standard library."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-16000:])
    return result.stdout


def probe(path: Path) -> dict:
    data = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate,codec_name,pix_fmt:format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    stream["fps"] = float(num) / float(den)
    stream["duration"] = float(data["format"]["duration"])
    if stream["duration"] <= 0:
        raise RuntimeError("视频时长无效")
    return stream


def sample(path: Path, duration: float) -> tuple[list[str], list[float]]:
    frames, timestamps = [], []
    for index, fraction in enumerate((0.125, 0.375, 0.625, 0.875)):
        frame = Path(f"frame_{index}.png")
        timestamp = duration * fraction
        command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(timestamp),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=-2:360",
                "-y",
                str(frame),
            ]
        )
        if not frame.exists() or frame.stat().st_size == 0:
            raise RuntimeError("抽帧失败")
        frames.append(frame.as_posix())
        timestamps.append(timestamp)
    return frames, timestamps


def main() -> None:
    try:
        action = sys.argv[1]
        if action == "render":
            class_name, quality = sys.argv[2:4]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "manim",
                    "render",
                    f"-q{quality}",
                    "--renderer=cairo",
                    "--disable_caching",
                    "--progress_bar=none",
                    "--verbosity=WARNING",
                    "--media_dir=/work/out",
                    "scene.py",
                    class_name,
                ],
                capture_output=True,
                text=True,
            )
            log = (result.stdout + "\n" + result.stderr)[-60000:]
            Path("render.log").write_text(log, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(log[-16000:])
            matches = list(Path("out").rglob(class_name + ".mp4"))
            if len(matches) != 1:
                raise RuntimeError("未找到唯一场景视频")
            video = matches[0]
        elif action == "assemble":
            videos = sorted(Path(".").glob("scene_*.mp4"))
            if not videos:
                raise RuntimeError("没有可合成的场景")
            for item in videos:
                info = probe(item)
                if info["width"] != 1280 or info["height"] != 720 or abs(info["fps"] - 30) > 0.01:
                    raise RuntimeError("正式场景尺寸或帧率不一致")
            Path("concat.txt").write_text(
                "".join(f"file '{v.name}'\n" for v in videos), encoding="utf-8"
            )
            video = Path("assembled.mp4")
            command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    "concat.txt",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(video),
                ]
            )
            command(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"])
        else:
            raise RuntimeError("未知渲染操作")
        info = probe(video)
        frames, timestamps = sample(video, info["duration"])
        output = {
            "status": "success",
            "video_path": video.as_posix(),
            "frames": frames,
            "timestamps": timestamps,
            "duration": info["duration"],
            "metadata": info,
        }
    except Exception as exc:
        message = str(exc)[-16000:]
        lower = message.lower()
        category = "manim_runtime"
        if "no such file" in lower or "not found" in lower or "no module named" in lower:
            category = "environment"
        elif "latex" in lower or "tex error" in lower:
            category = "latex"
        elif "syntaxerror" in lower or "nameerror" in lower or "typeerror" in lower:
            category = "python"
        output = {"status": "error", "error_category": category, "message": message}
    Path("result.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
