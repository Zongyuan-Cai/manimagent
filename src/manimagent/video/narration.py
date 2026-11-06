"""Measure complete spoken beats and place PCM audio on the visual timeline.

Beat boundaries are measured; word/cue positions remain estimates until an
independent forced aligner is introduced. Never truncate speech to fit a scene.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

from manimagent.errors import ContentError
from manimagent.video.timeline import speech_seconds


def narrate_plan(
    plan, synthesizer, directory: Path, budget, progress=lambda message: None, evidence=None
):
    plan = plan.model_copy(deep=True)
    directory.mkdir(parents=True, exist_ok=True)
    clips = []
    if evidence is None:
        evidence = []
    total = 0.0
    count = sum(len(s.beats) for s in plan.scenes)
    for scene in plan.scenes:
        clock = 0.0
        for beat in scene.beats:
            budget.remaining()
            index = len(clips)
            progress(f"正在合成 MiMo 旁白 {index + 1}/{count}")
            path = directory / f"beat_{index:03d}.wav"
            info = synthesizer.synthesize(beat.spoken_text, path, budget)
            evidence.append(
                {
                    **info,
                    "path": path.name,
                    "scene_id": scene.scene_id,
                    "beat_id": beat.beat_id,
                    "start_seconds": total + clock + 1,
                    "end_seconds": total + clock + 1 + info["duration_seconds"],
                    "cue_time_source": "estimated",
                }
            )
            # Retain the already-reviewed minimum reading window; extend if speech needs it.
            duration = (
                math.ceil(
                    max(beat.end_seconds - beat.start_seconds, info["duration_seconds"] + 2) * 30
                )
                / 30
            )
            if duration > 30:
                raise ContentError(
                    "tts_pacing", "旁白单节拍超过30秒，请拆分讲解", state="needs_review"
                )
            prefix = beat.spoken_text[: beat.spoken_text.index(beat.cue_text)]
            ratio = speech_seconds(prefix) / max(speech_seconds(beat.spoken_text), 0.01)
            beat.start_seconds = clock
            beat.stable_seconds = clock + 1
            beat.narration_end_seconds = clock + 1 + info["duration_seconds"]
            beat.end_seconds = clock + duration
            beat.cue_seconds = clock + 1 + ratio * info["duration_seconds"]
            beat.time_source = "audio_measured"
            clips.append((path, total + beat.stable_seconds))
            clock += duration
            if total + clock > 360:
                raise ContentError(
                    "tts_pacing", "实测旁白使视频超过360秒，请拆分主题", state="needs_review"
                )
        scene.duration_seconds = round(clock, 6)
        total += scene.duration_seconds
    if not 120 <= total <= 360:
        raise ContentError("tts_pacing", "实测旁白时间线不在120～360秒范围", state="needs_review")
    plan.duration_seconds = total
    plan.outline.duration_reason = f"根据实测旁白与已检查的阅读窗口排程，总计{total:.2f}秒。"
    plan.timing_revision += 1
    plan.time_source = "audio_measured"
    plan.profile_id = "standard_narrated"
    output = directory / "narration.wav"
    compose_pcm(clips, output, total, budget)
    return plan, output, evidence


def compose_pcm(clips, output: Path, duration: float, budget):
    """Stream silence and PCM clips; reject overlaps or format changes."""
    with wave.open(str(clips[0][0]), "rb") as first:
        channels, width, rate = first.getnchannels(), first.getsampwidth(), first.getframerate()
    frame_bytes = channels * width
    cursor = 0
    with wave.open(str(output), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(width)
        target.setframerate(rate)

        def silence(frames):
            while frames:
                budget.remaining()
                count = min(frames, rate)
                target.writeframesraw((b"\x80" if width == 1 else b"\x00") * (count * frame_bytes))
                frames -= count

        for path, start in clips:
            budget.remaining()
            position = round(start * rate)
            if position < cursor:
                raise ContentError("tts_timing", "旁白片段时间重叠")
            silence(position - cursor)
            with wave.open(str(path), "rb") as clip:
                if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (
                    channels,
                    width,
                    rate,
                ):
                    raise ContentError("tts_format", "旁白片段音频格式不一致")
                cursor = position + clip.getnframes()
                while data := clip.readframes(rate):
                    budget.remaining()
                    target.writeframesraw(data)
        end = round(duration * rate)
        if cursor > end:
            raise ContentError("tts_timing", "旁白超过视频终点")
        silence(end - cursor)
