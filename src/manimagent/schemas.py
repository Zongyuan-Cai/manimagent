from __future__ import annotations

import keyword
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
FINAL_STATUSES = {"completed", "needs_input", "needs_review", "failed", "interrupted"}


def now() -> str:
    return datetime.now(UTC).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationRequest(Contract):
    text: StrictStr

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 3000:
            raise ValueError("请输入 1 至 3000 个字符的学习内容")
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value) or "\0" in value:
            raise ValueError("文本包含无效字符")
        return value


class RequiredPoint(Contract):
    point_id: str = Field(pattern=ID_PATTERN)
    description: str = Field(min_length=1, max_length=1000)


class SceneStep(Contract):
    step_id: str = Field(pattern=ID_PATTERN)
    explanation: str = Field(min_length=1, max_length=2000)
    visual_action: str = Field(min_length=1, max_length=2000)


class SceneSpec(Contract):
    scene_id: str = Field(pattern=ID_PATTERN)
    class_name: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]{0,63}$")
    goal: str = Field(min_length=1, max_length=2000)
    covered_point_ids: list[str] = Field(min_length=1, max_length=20)
    steps: list[SceneStep] = Field(min_length=1, max_length=12)
    formulas: list[str] = Field(default_factory=list, max_length=20)
    duration_seconds: float = Field(default=20, ge=1, le=90)

    @field_validator("scene_id", "class_name")
    @classmethod
    def portable_name(cls, value: str) -> str:
        reserved = {"con", "prn", "aux", "nul"} | {
            f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)
        }
        if value.lower() in reserved or keyword.iskeyword(value):
            raise ValueError("标识不能使用保留名称")
        return value


class LessonPlan(Contract):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    given_facts: list[str] = Field(max_length=30)
    example_notes: list[str] = Field(default_factory=list, max_length=20)
    required_points: list[RequiredPoint] = Field(min_length=1, max_length=20)
    scenes: list[SceneSpec] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_references(self) -> LessonPlan:
        groups = [
            [s.scene_id for s in self.scenes],
            [s.class_name for s in self.scenes],
            [p.point_id for p in self.required_points],
            [step.step_id for s in self.scenes for step in s.steps],
        ]
        if any(len(ids) != len({name.casefold() for name in ids}) for ids in groups):
            raise ValueError("场景、类名、知识点及步骤标识必须唯一")
        points = {p.point_id for p in self.required_points}
        covered = {p for s in self.scenes for p in s.covered_point_ids}
        if points != covered:
            raise ValueError("知识点引用无效，或有必需知识点未分配场景")
        return self


class PlanResponse(Contract):
    status: Literal["ready", "needs_input"]
    message: str = ""
    plan: LessonPlan | None = None

    @model_validator(mode="after")
    def validate_status(self) -> PlanResponse:
        if self.status == "ready" and self.plan is None:
            raise ValueError("ready 必须提供 plan")
        if self.status == "needs_input" and not self.message.strip():
            raise ValueError("needs_input 必须说明需要补充什么")
        return self


class PlanCheck(Contract):
    decision: Literal["pass", "revise", "needs_input"]
    message: str = ""


class CodeResponse(Contract):
    code: str = Field(min_length=1, max_length=80000)


class Issue(Contract):
    severity: Literal["info", "minor", "major", "critical"]
    description: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=2000)
    suggested_fix: str = ""
    step_id: str | None = None
    frame_time: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Scores(Contract):
    logic_flow: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)
    layout_occlusion: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)
    accuracy: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)

    @property
    def mean(self) -> float:
        return (self.logic_flow + self.layout_occlusion + self.accuracy) / 3


class ReviewReport(Contract):
    review_status: Literal["valid", "unavailable", "invalid"] = "valid"
    decision: Literal["pass", "revise", "fail"]
    scores: Scores | None = None
    issues: list[Issue] = Field(default_factory=list, max_length=30)
    required_point_coverage: dict[str, StrictBool] = Field(default_factory=dict)
    requires_replanning: bool = False
    message: str = ""

    @model_validator(mode="after")
    def check_scores(self) -> ReviewReport:
        if self.review_status == "valid" and self.scores is None:
            raise ValueError("有效检查必须包含全部三项评分")
        return self

    def qualifies(self, scene: SceneSpec) -> bool:
        return (
            self.review_status == "valid"
            and self.decision == "pass"
            and not self.requires_replanning
            and not any(i.severity in {"major", "critical"} for i in self.issues)
            and all(self.required_point_coverage.get(p) is True for p in scene.covered_point_ids)
        )


class Artifact(Contract):
    artifact_id: str
    path: str
    kind: str
    label: str


class RenderResult(Contract):
    status: Literal["success", "error"]
    error_category: str = ""
    message: str = ""
    video_path: str | None = None
    frames: list[str] = Field(default_factory=list)
    timestamps: list[float] = Field(default_factory=list)
    duration: float = Field(default=0, ge=0, allow_inf_nan=False)


class Candidate(Contract):
    attempt_id: str
    scene_id: str
    plan_revision: int = 1
    code_artifact_id: str
    render: RenderResult
    review: ReviewReport | None = None
    video_artifact_id: str | None = None


class SceneResult(Contract):
    scene_id: str
    status: Literal["pending", "completed", "needs_review", "failed"] = "pending"
    candidates: list[Candidate] = Field(default_factory=list)
    selected_attempt_id: str | None = None
    formal_video_artifact_id: str | None = None
    formal_review: ReviewReport | None = None
    execution_repairs: int = 0
    visual_revisions: int = 0
    message: str = ""


class Usage(Contract):
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_reported: bool = False
    elapsed_seconds: float = 0


class RunManifest(Contract):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal[
        "queued", "running", "completed", "needs_input", "needs_review", "failed", "interrupted"
    ] = "queued"
    stage: str = "queued"
    title: str = ""
    message: str = "等待生成"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    plan_revision: int = 1
    total_scenes: int = 0
    scene_results: list[SceneResult] = Field(default_factory=list)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    final_artifact_id: str | None = None


def is_job_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{32}", value))
