"""Bounded, declarative animation contracts. Model output is data, never Python."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from manimagent.schemas import Contract, ResourceID, now

PROMPT_VERSION = "silent-templates-2"
TEMPLATE_VERSION = "native-manim-1"
SmallText = Annotated[str, StringConstraints(min_length=1, max_length=120)]
LocalID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")]
Coordinate = Annotated[float, Field(ge=-20, le=20, allow_inf_nan=False)]
Point = tuple[Coordinate, Coordinate]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TeachingUnit(Contract):
    unit_id: LocalID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    sha256: str


class SceneOutline(Contract):
    scene_id: LocalID
    title: Annotated[str, StringConstraints(min_length=1, max_length=26)]
    objective: SmallText
    unit_ids: list[LocalID] = Field(min_length=1, max_length=50)
    beat_count: int = Field(ge=3, le=7)


class VideoOutline(Contract):
    status: Literal["ready", "answer_issue", "scope_issue", "needs_input"]
    message: SmallText
    title: Annotated[str, StringConstraints(min_length=1, max_length=26)]
    audience: SmallText
    difficulty: Literal["basic", "intermediate", "advanced"]
    difficulty_reason: SmallText
    duration_reason: SmallText
    scenes: list[SceneOutline] = Field(max_length=8)


class GraphicObject(Contract):
    object_id: LocalID
    kind: Literal["vector", "point", "segment", "dashed", "polygon", "circle"]
    points: list[Point] = Field(min_length=1, max_length=8)
    label: Annotated[str, StringConstraints(max_length=18)] | None = None
    color: Literal["blue", "gold", "teal", "red", "white", "muted"] = "blue"
    radius: float = Field(default=0.2, gt=0, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_shape(self):
        count = len(self.points)
        if self.kind in {"vector", "segment", "dashed"}:
            if count != 2 or math.dist(*self.points) < 1e-5:
                raise ValueError("线段和向量需要两个不同的点")
        elif self.kind == "polygon":
            if count < 3:
                raise ValueError("多边形至少三个点")
        elif count != 1:
            raise ValueError("点和圆需要一个中心点")
        return self


class Diagram(Contract):
    kind: Literal["plane", "matrix", "none"] = "none"
    x_range: tuple[Coordinate, Coordinate] = (-1, 5)
    y_range: tuple[Coordinate, Coordinate] = (-1, 4)
    objects: list[GraphicObject] = Field(default_factory=list, max_length=10)
    matrix: list[list[float]] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def bounded_diagram(self):
        for low, high in [self.x_range, self.y_range]:
            if high - low < 1 or high - low > 30:
                raise ValueError("坐标范围无效")
        ids = [o.object_id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("图形对象 ID 重复")
        if self.kind == "none" and (self.objects or self.matrix):
            raise ValueError("无图形模式不能附带图形")
        if self.kind == "matrix":
            if not self.matrix or len({len(r) for r in self.matrix}) != 1:
                raise ValueError("矩阵不能为空或不等宽")
            if any(
                not 1 <= len(r) <= 4 or any(not math.isfinite(x) or abs(x) > 1e6 for x in r)
                for r in self.matrix
            ):
                raise ValueError("矩阵超出模板限制")
            if self.objects:
                raise ValueError("矩阵模板不能混入坐标图")
        if self.kind == "plane":
            if self.matrix:
                raise ValueError("坐标图不能附带矩阵")
            for obj in self.objects:
                for x, y in obj.points:
                    if (
                        not self.x_range[0] <= x <= self.x_range[1]
                        or not self.y_range[0] <= y <= self.y_range[1]
                    ):
                        raise ValueError("对象坐标超出坐标范围")
        return self


class BeatDraft(Contract):
    beat_id: LocalID
    unit_ids: list[LocalID] = Field(min_length=1, max_length=50)
    headline: Annotated[str, StringConstraints(min_length=1, max_length=24)]
    screen_text: Annotated[str, StringConstraints(min_length=8, max_length=100)]
    formulas: list[Annotated[str, StringConstraints(min_length=1, max_length=130)]] = Field(
        default_factory=list, max_length=3
    )
    spoken_text: Annotated[str, StringConstraints(min_length=15, max_length=150)]
    cue_text: Annotated[str, StringConstraints(min_length=1, max_length=30)]
    cue_objects: list[LocalID] = Field(default_factory=list, max_length=10)
    reading_reason: SmallText
    diagram: Diagram = Field(default_factory=Diagram)

    @field_validator("formulas")
    @classmethod
    def safe_formulas(cls, values):
        normalized = []
        for value in values:
            value = re.sub(r"(?<![A-Za-z\\])(?:dfrac|tfrac|frac)(?=\{)", r"\\frac", value)
            if (
                "$" in value
                or any(ord(c) > 127 for c in value)
                or re.search(
                    r"\\(?:input|include|write|open|read|usepackage|newcommand|def|catcode|special|begin|end)\b",
                    value,
                )
            ):
                raise ValueError("公式仅接受 ASCII MathText，不接受文件、宏或 LaTeX 环境")
            for source, target in [
                (r"\lVert", r"\Vert"),
                (r"\rVert", r"\Vert"),
                (r"\lvert", r"\vert"),
                (r"\rvert", r"\vert"),
                (r"\dfrac", r"\frac"),
                (r"\tfrac", r"\frac"),
            ]:
                value = value.replace(source, target)
            normalized.append(value)
        return normalized

    @model_validator(mode="after")
    def real_cue(self):
        if self.cue_text not in self.spoken_text:
            raise ValueError("cue_text 必须是旁白中的原文片段")
        if not set(self.cue_objects) <= {o.object_id for o in self.diagram.objects}:
            raise ValueError("cue 引用了不存在的图形对象")
        return self


class SceneDraft(Contract):
    scene_id: LocalID
    beats: list[BeatDraft] = Field(min_length=3, max_length=7)


class ResolvedBeat(BeatDraft):
    start_seconds: float
    stable_seconds: float
    end_seconds: float
    narration_end_seconds: float
    cue_seconds: float
    time_source: Literal["estimated"] = "estimated"


class PlannedScene(SceneOutline):
    beats: list[ResolvedBeat]
    duration_seconds: float


class VideoPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: ResourceID
    answer_id: ResourceID
    answer_hash: str
    narration_hash: str
    timing_revision: int = 1
    prompt_version: str = PROMPT_VERSION
    template_version: str = TEMPLATE_VERSION
    profile_id: Literal["standard_silent"] = "standard_silent"
    time_source: Literal["estimated"] = "estimated"
    outline: VideoOutline
    teaching_units: list[TeachingUnit]
    scenes: list[PlannedScene]
    duration_seconds: float


class UnitCheck(Contract):
    unit_id: LocalID
    decision: Literal["pass", "fail", "unknown"]
    scene_ids: list[LocalID]
    evidence: SmallText


class PlanCheck(Contract):
    decision: Literal["pass", "revise", "fail"]
    message: SmallText
    units: list[UnitCheck]
    problem_scene_ids: list[LocalID] = Field(default_factory=list)


class VideoRequest(Contract):
    answer_id: ResourceID
    video_profile_id: Literal["standard_silent"] = "standard_silent"
    resume_job_id: ResourceID | None = None


class VideoResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    video_id: ResourceID
    answer_id: ResourceID
    answer_hash: str
    plan_id: ResourceID
    video_asset_id: ResourceID
    subtitle_asset_id: None = None
    audio_asset_id: None = None
    duration_seconds: float
    time_source: Literal["estimated"] = "estimated"
    review_mode: Literal["trusted_template_geometry_and_layout"] = (
        "trusted_template_geometry_and_layout"
    )
    visual_model_review: Literal["not_run"] = "not_run"
    step_mapping: list[dict]
    unit_mapping: list[dict]
    report_asset_ids: list[ResourceID]
    frame_mapping: list[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
