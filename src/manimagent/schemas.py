"""Versioned contracts for the material-to-answer API (independent of video)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200000)]
ResourceID = Annotated[str, StringConstraints(pattern=r"^[a-z]+_[a-f0-9]{32}$")]


def now() -> str:
    return datetime.now(UTC).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def valid_unicode(cls, value):
        if isinstance(value, str):
            value.encode("utf-8")
            if not value.strip() or "\x00" in value:
                raise ValueError("字符串不能为空或包含 NUL")
        return value


class Error(Contract):
    code: str
    message: str
    retryable: bool = False
    details: dict = Field(default_factory=dict)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_reported: bool = False
    elapsed_seconds: float = 0


class Ref(Contract):
    type: Literal["generation", "material", "answer", "video"]
    id: ResourceID


class FileAsset(Contract):
    schema_version: Literal["1.0"] = "1.0"
    file_id: ResourceID
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    created_at: str = Field(default_factory=now)


class Selection(Contract):
    kind: Literal["pages", "slides"]
    start: int = Field(ge=1, strict=True)
    end: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("范围终点不能小于起点")
        return self


class TextInput(Contract):
    type: Literal["text"]
    text: NonEmpty


class FileInput(Contract):
    type: Literal["file"]
    file_id: ResourceID
    selection: Selection | None = None


ParseInput = Annotated[TextInput | FileInput, Field(discriminator="type")]


class ParseRequest(Contract):
    inputs: list[ParseInput] = Field(min_length=1, max_length=20)
    parser_profile_id: Literal["parser_default"] | None = None
    input_mode: Literal["auto", "local", "native"] = "auto"


class SourceLocation(Contract):
    input_index: int = Field(ge=0)
    file_id: ResourceID | None = None
    locator: dict[str, int] | None = None
    verification: Literal["verified", "unverified", "unavailable"] = "verified"


class MaterialBlock(Contract):
    block_id: ResourceID
    kind: Literal["heading", "text", "formula", "table", "image"] = "text"
    content: str
    asset_ids: list[ResourceID] = Field(default_factory=list)
    source: SourceLocation
    review_state: Literal["verified", "unverified", "needs_review"] = "unverified"


class Notice(Contract):
    code: str
    message: str
    block_ids: list[ResourceID] = Field(default_factory=list)


class MaterialIR(Contract):
    schema_version: Literal["1.0"] = "1.0"
    material_id: ResourceID
    source_file_ids: list[ResourceID]
    blocks: list[MaterialBlock] = Field(min_length=1)
    warnings: list[Notice] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class SourceRef(Contract):
    material_id: ResourceID
    block_id: ResourceID


class AnswerNotice(Contract):
    code: str
    message: str
    step_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class AnswerStep(Contract):
    step_id: str
    title: str
    content: NonEmpty
    source_refs: list[SourceRef]
    example_notes: list[str] = Field(default_factory=list)


class Answer(Contract):
    schema_version: Literal["1.0"] = "1.0"
    answer_id: ResourceID
    material_ids: list[ResourceID] = Field(min_length=1)
    derived_from_answer_id: ResourceID | None = None
    title: str
    instruction: NonEmpty
    markdown: NonEmpty
    steps: list[AnswerStep] = Field(min_length=1)
    warnings: list[AnswerNotice] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class AnswerRequest(Contract):
    material_ids: list[ResourceID] = Field(min_length=1, max_length=20)
    instruction: NonEmpty
    answer_profile_id: Literal["answer_default"] | None = None

    @field_validator("material_ids")
    @classmethod
    def unique_materials(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("材料 ID 不能重复")
        return value


class AnswerRevisionRequest(Contract):
    base_answer_id: ResourceID
    markdown: NonEmpty
    warnings: list[AnswerNotice] = Field(default_factory=list)


class InputsSource(ParseRequest):
    type: Literal["inputs"]
    instruction: NonEmpty
    answer_profile_id: Literal["answer_default"] | None = None


class MaterialsSource(AnswerRequest):
    type: Literal["materials"]


class AnswerSource(Contract):
    type: Literal["answer"]
    answer_id: ResourceID


class TextGenerationRequest(Contract):
    output_mode: Literal["text", "both"] = "text"
    source: Annotated[InputsSource | MaterialsSource | AnswerSource, Field(discriminator="type")]


class StageState(Contract):
    status: Literal[
        "pending",
        "running",
        "completed",
        "blocked",
        "needs_input",
        "needs_review",
        "failed",
        "interrupted",
    ] = "pending"
    error: Error | None = None
    diagnostic_asset_ids: list[ResourceID] = Field(default_factory=list)


class Generation(Contract):
    schema_version: Literal["1.0"] = "1.0"
    generation_id: ResourceID
    output_mode: Literal["text", "both"] = "text"
    source: dict
    material_ids: list[ResourceID] = Field(default_factory=list)
    answer_id: ResourceID | None = None
    video_id: ResourceID | None = None
    stages: dict[str, StageState] = Field(
        default_factory=lambda: {"material": StageState(), "answer": StageState()}
    )
    active_job_id: ResourceID
    job_ids: list[ResourceID]
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class Job(Contract):
    schema_version: Literal["1.0"] = "1.0"
    job_id: ResourceID
    kind: Literal["generation", "parse", "answer", "video"]
    status: Literal[
        "queued",
        "running",
        "completed",
        "partial",
        "needs_input",
        "needs_review",
        "failed",
        "interrupted",
    ] = "queued"
    stage: str = "queued"
    message: str = "等待处理"
    resource_ref: Ref | None = None
    result: Ref | None = None
    error: Error | None = None
    diagnostic_asset_ids: list[ResourceID] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class AnswerCheck(Contract):
    decision: Literal["pass", "needs_input", "needs_review"]
    message: str
