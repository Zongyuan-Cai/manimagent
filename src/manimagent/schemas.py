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

from manimagent.memory.models import MemoryOptions

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
    # Only supplier-reported cache hits; None distinguishes unknown from a known zero.
    cached_input_tokens: int | None = Field(default=None, ge=0, strict=True)
    cache_reported_calls: int = Field(default=0, ge=0, strict=True)
    cache_accounted_input_tokens: int = Field(default=0, ge=0, strict=True)


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
    model_config = ConfigDict(extra="forbid", frozen=True)
    material_id: ResourceID
    block_id: ResourceID


class SourceBlock(Contract):
    """Accepted material text with immutable, lossless source-location metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    block_id: ResourceID
    kind: Literal["heading", "text", "formula", "table", "image"]
    content: NonEmpty
    input_index: int = Field(ge=0)
    file_id: ResourceID | None = None
    locator: tuple[tuple[str, int], ...] = ()
    verification: Literal["verified", "unverified", "unavailable"]
    review_state: Literal["verified", "unverified"]


class SourceWarning(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    block_ids: tuple[ResourceID, ...] = ()


class SourceFile(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    file_id: ResourceID
    filename: str
    media_type: str


class SourceMaterial(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    material_id: ResourceID
    blocks: tuple[SourceBlock, ...] = Field(min_length=1)
    warnings: tuple[SourceWarning, ...] = ()
    source_files: tuple[SourceFile, ...] = ()


class SourceBundle(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["source-bundle-1"] = "source-bundle-1"
    materials: tuple[SourceMaterial, ...] = Field(min_length=1)
    allowed_source_refs: tuple[SourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_sources(self):
        actual = [(m.material_id, b.block_id) for m in self.materials for b in m.blocks]
        allowed = [(ref.material_id, ref.block_id) for ref in self.allowed_source_refs]
        if (
            len(actual) != len(set(actual))
            or len(allowed) != len(set(allowed))
            or set(actual) != set(allowed)
        ):
            raise ValueError("来源合同必须逐一对应完整材料块，不得重复或伪造引用")
        return self


class AnswerUnit(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unit_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    content: NonEmpty
    source_refs: tuple[SourceRef, ...] = Field(min_length=1)
    depends_on: tuple[str, ...]
    required: bool = Field(strict=True)


class AnswerDocument(Contract):
    """One fact source for the readable answer and every downstream teaching unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["answer-document-1"] = "answer-document-1"
    origin: Literal["structured", "legacy_markdown"] = "structured"
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    units: tuple[AnswerUnit, ...] = Field(min_length=1, max_length=70)

    @model_validator(mode="after")
    def coherent_units(self):
        if self.origin == "structured" and any(
            "\n" in title or "\r" in title or "[[" in title or "]]" in title
            for title in (self.title, *(unit.title for unit in self.units))
        ):
            raise ValueError("结构化标题必须是单行，来源引用不能藏在标题中")
        seen = set()
        for unit in self.units:
            if unit.unit_id in seen:
                raise ValueError("教学单元 ID 必须唯一")
            if (
                len(unit.depends_on) != len(set(unit.depends_on))
                or not set(unit.depends_on) <= seen
            ):
                raise ValueError("单元依赖必须引用已声明的先前单元，不能缺失或循环")
            refs = [(ref.material_id, ref.block_id) for ref in unit.source_refs]
            if len(refs) != len(set(refs)):
                raise ValueError("单元来源不能重复")
            if self.origin == "structured" and ("[[" in unit.content or "]]" in unit.content):
                raise ValueError("来源放入 source_refs，由程序生成引用标记")
            seen.add(unit.unit_id)
        if not any(unit.required for unit in self.units):
            raise ValueError("回答必须包含必要教学内容")
        if self.origin == "legacy_markdown" and (len(self.units) != 1 or self.units[0].depends_on):
            raise ValueError("旧 Markdown 只允许完整原文单元，不推测分段或依赖")
        if sum(len(unit.content) for unit in self.units) > 180000:
            raise ValueError("完整回答超过支持的正文容量，请拆分主题")
        return self


class AnswerCheck(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: Literal["pass", "needs_input", "needs_review"]
    message: str


class VerifiedAnswer(Contract):
    """A check receipt bound to the exact document, source snapshot and policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["verified-answer-1"] = "verified-answer-1"
    answer_id: ResourceID
    document_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    source_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    policy_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    checker_profile_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    decision: Literal["pass"] = "pass"
    report: AnswerCheck
    checked_at: str = Field(default_factory=now)

    @model_validator(mode="after")
    def passed(self):
        if self.report.decision != "pass":
            raise ValueError("未通过检查的回答不能生成放行凭据")
        return self


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
    memory_snapshot: dict | None = None
    request_intent: dict | None = None
    effective_profile: dict | None = None
    document: AnswerDocument | None = None
    verification: VerifiedAnswer | None = None
    created_at: str = Field(default_factory=now)


class AnswerRequest(Contract):
    material_ids: list[ResourceID] = Field(min_length=1, max_length=20)
    instruction: NonEmpty
    answer_profile_id: Literal["answer_default"] | None = None
    memory: MemoryOptions | None = None

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
    video_profile_id: Literal["auto", "standard_silent", "standard_narrated"] = "auto"
    source: Annotated[InputsSource | MaterialsSource | AnswerSource, Field(discriminator="type")]
    memory: MemoryOptions | None = None

    @model_validator(mode="after")
    def memory_binding(self):
        if self.source.type == "answer" and self.memory is not None:
            raise ValueError("已有回答沿用原记忆快照；需要新偏好时请重新生成回答")
        if self.memory is not None and getattr(self.source, "memory", None) is not None:
            raise ValueError("生成任务只能指定一份记忆选项")
        return self


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
    video_profile_id: Literal["auto", "standard_silent", "standard_narrated"] = "auto"
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
    answer_id: ResourceID | None = None
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
    memory_save: dict | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
