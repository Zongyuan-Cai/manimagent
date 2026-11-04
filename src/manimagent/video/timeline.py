"""Pure narration estimates, bounded beat timing and complete plan construction."""

from __future__ import annotations

import json
import math
import re

from manimagent.content_store import new_id
from manimagent.errors import ContentError
from manimagent.video.models import PlannedScene, ResolvedBeat, SceneDraft, VideoPlan, digest


def speech_seconds(text: str) -> float:
    return (
        sum(1 for c in text if ord(c) > 127 and c.isalnum()) / 4
        + len(re.findall(r"[A-Za-z0-9]+", text)) / 2.6
    )


def resolve_scene(outline, draft: SceneDraft) -> PlannedScene:
    if draft.scene_id != outline.scene_id or len(draft.beats) != outline.beat_count:
        raise ContentError("invalid_plan", "分块场景或节拍数量不匹配", state="needs_review")
    if len({b.beat_id for b in draft.beats}) != len(draft.beats):
        raise ContentError("invalid_plan", "节拍ID重复", state="needs_review")
    expected = set(outline.unit_ids)
    covered = {u for b in draft.beats for u in b.unit_ids}
    if covered != expected:
        raise ContentError(
            "video_coverage", "分块未覆盖指定教学单元或引入了其他单元", state="needs_review"
        )
    beats, clock = [], 0.0
    for beat in draft.beats:
        narration = max(3, speech_seconds(beat.spoken_text))
        reading = (
            max(5, (len(beat.screen_text) + len(beat.headline)) / 5) + len(beat.formulas) * 1.3
        )
        duration = math.ceil((1 + max(narration, reading) + 1) * 30) / 30
        if duration > 24:
            raise ContentError(
                "video_pacing", "单个节拍过密，请拆分或减少同屏文字", state="needs_review"
            )
        prefix = beat.spoken_text[: beat.spoken_text.index(beat.cue_text)]
        cue = clock + 1 + speech_seconds(prefix)
        if cue + 0.5 > clock + duration:
            raise ContentError("video_pacing", "语义锚点超出节拍窗口", state="needs_review")
        beats.append(
            ResolvedBeat(
                **beat.model_dump(),
                start_seconds=clock,
                stable_seconds=clock + 1,
                end_seconds=clock + duration,
                narration_end_seconds=clock + 1 + narration,
                cue_seconds=cue,
            )
        )
        clock += duration
    return PlannedScene(**outline.model_dump(), beats=beats, duration_seconds=round(clock, 6))


def build_plan(answer, outline, units, scenes):
    duration = sum(s.duration_seconds for s in scenes)
    seen = set()
    for scene in scenes:
        for beat in scene.beats:
            signature = digest(
                json.dumps(
                    {
                        "text": beat.screen_text,
                        "formulas": beat.formulas,
                        "diagram": beat.diagram.model_dump(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            if signature in seen:
                raise ContentError(
                    "video_pacing",
                    "多个节拍内容完全重复，不能用于凑时长",
                    state="needs_review",
                )
            seen.add(signature)
    if not 120 <= duration <= 360:
        raise ContentError(
            "video_pacing",
            f"教学时间解析为 {duration:.1f} 秒，未落在120～360秒范围；不能补空等凑时长",
            state="needs_review",
        )
    return VideoPlan(
        plan_id=new_id("plan"),
        answer_id=answer.answer_id,
        answer_hash=digest(answer.markdown),
        narration_hash=digest(
            json.dumps([b.spoken_text for s in scenes for b in s.beats], ensure_ascii=False)
        ),
        outline=outline,
        teaching_units=units,
        scenes=scenes,
        duration_seconds=duration,
    )
