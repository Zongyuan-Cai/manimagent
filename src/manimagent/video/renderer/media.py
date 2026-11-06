"""Video probing, keyframe evidence and lossless scene assembly."""

from __future__ import annotations

import subprocess
from pathlib import Path


def probe(path: Path, decode: bool = False, *, expect_audio: bool = False) -> dict:
    import av

    with av.open(str(path)) as container:
        if len(container.streams.video) != 1 or len(container.streams.audio) != int(expect_audio):
            raise ValueError("视频或音频流数量不符合预期")
        stream = container.streams.video[0]
        duration = (
            float(stream.duration * stream.time_base)
            if stream.duration
            else float(container.duration / av.time_base)
        )
        metadata = {
            "width": stream.width,
            "height": stream.height,
            "fps": float(stream.average_rate),
            "codec": stream.codec_context.name,
            "pix_fmt": stream.codec_context.pix_fmt,
            "duration_seconds": duration,
            "audio_streams": len(container.streams.audio),
        }
        if (
            metadata["width"] != 1280
            or metadata["height"] != 720
            or abs(metadata["fps"] - 30) > 0.01
        ):
            raise ValueError("视频规格必须为 1280×720 / 30 fps")
        if metadata["codec"] != "h264" or metadata["pix_fmt"] != "yuv420p" or duration <= 0:
            raise ValueError("视频编码或时长无效")
        if decode:
            count = 0
            last_time = -1
            for frame in container.decode(video=0):
                timestamp = float(frame.pts * frame.time_base)
                if timestamp <= last_time:
                    raise ValueError("解码时间戳不递增")
                last_time = timestamp
                count += 1
            if abs(count / 30 - duration) > 0.15:
                raise ValueError("完整解码帧数与时长不一致")
            metadata["decoded_frames"] = count
    if expect_audio:
        with av.open(str(path)) as container:
            audio = container.streams.audio[0]
            audio_duration = float(audio.duration * audio.time_base)
            audio_start = float((audio.start_time or 0) * audio.time_base)
            if (
                audio.codec_context.name != "aac"
                or abs(audio_duration - duration) > 0.15
                or abs(audio_start) > 0.1
            ):
                raise ValueError("音频编码、起点或时长与视频不匹配")
            metadata.update(
                audio_duration_seconds=audio_duration,
                audio_codec=audio.codec_context.name,
                audio_sample_rate=audio.codec_context.sample_rate,
            )
            if decode:
                samples, last = 0, -1.0
                for frame in container.decode(audio=0):
                    timestamp = float(frame.pts * frame.time_base)
                    if timestamp <= last:
                        raise ValueError("音频解码时间戳不递增")
                    samples += frame.samples
                    last = timestamp
                if abs(samples / audio.codec_context.sample_rate - duration) > 0.15:
                    raise ValueError("音频完整解码长度与视频不符")
                metadata["decoded_audio_samples"] = samples
    return metadata


def mux_audio(data: dict, directory: Path) -> dict:
    import imageio_ffmpeg

    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-i",
            "complete.mp4",
            "-i",
            "narration.wav",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-y",
            "narrated.mp4",
        ],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("旁白封装失败")
    media = probe(directory / "narrated.mp4", decode=True, expect_audio=True)
    if abs(media["duration_seconds"] - data["duration_seconds"]) > 0.15:
        raise ValueError("旁白封装后视频时间线发生变化")
    return {"status": "success", "video_path": "narrated.mp4", "media": media}


def extract(path: Path, times: list[float], directory: Path) -> list[dict]:
    import av

    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for i, target in enumerate(times):
            container.seek(max(0, int(target / stream.time_base)), stream=stream)
            for frame in container.decode(video=0):
                actual = float(frame.pts * frame.time_base)
                if actual + 0.02 >= target:
                    dest = directory / f"frame_{i:03d}.png"
                    frame.to_image().save(dest)
                    frames.append(
                        {"path": dest.name, "requested_seconds": target, "actual_seconds": actual}
                    )
                    break
    if len(frames) != len(times):
        raise ValueError("关键节拍抽帧不完整")
    return frames


def assemble(data: dict, directory: Path) -> dict:
    import imageio_ffmpeg

    videos = [directory / name for name in data["videos"]]
    if any(p.parent != directory or not p.is_file() for p in videos):
        raise ValueError("合成只接受本目录的场景文件")
    infos = [probe(p) for p in videos]
    (directory / "concat.txt").write_text(
        "".join(f"file '{p.name}'\n" for p in videos), encoding="utf-8"
    )
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            "concat.txt",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            "complete.mp4",
        ],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("视频拼接失败：" + result.stderr.decode(errors="replace")[-1200:])
    metadata = probe(directory / "complete.mp4", decode=True)
    boundaries, total = [], 0.0
    for info in infos[:-1]:
        total += info["duration_seconds"]
        boundaries.extend([max(0, total - 0.1), total + 0.1])
    frames = extract(
        directory / "complete.mp4",
        boundaries + [max(0, metadata["duration_seconds"] - 0.5)],
        directory,
    )
    return {"status": "success", "video_path": "complete.mp4", "media": metadata, "frames": frames}
