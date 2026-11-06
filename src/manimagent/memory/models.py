"""Contracts for evidence-backed, two-layer user memory."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MemoryIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[\w.-]+$")
]


class MemoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryOptions(MemoryContract):
    session_id: MemoryIdentifier
    scope: MemoryIdentifier = "global"
    learn: bool = True


class MemoryCandidate(MemoryContract):
    field: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)
    evidence: str = Field(min_length=1, max_length=500)
    temporary: bool = False

    @field_validator("field", "value", "evidence")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("记忆内容不能为空或包含控制字符")
        return value


class MemoryPolicy(MemoryContract):
    promotion_sessions: int = Field(default=3, ge=2, le=20)
    short_term_days: int = Field(default=14, ge=1, le=365)
    max_entries: int = Field(default=200, ge=1, le=10000)
    max_evidence_per_entry: int = Field(default=20, ge=1, le=100)
    max_snapshot_entries: int = Field(default=12, ge=1, le=100)
    max_snapshot_chars: int = Field(default=6000, ge=256, le=50000)
    max_sources: int = Field(default=10000, ge=1, le=1000000)


class MemoryEvidence(MemoryContract):
    source_id: str
    session_id: str
    quote: str
    date: str
    kind: Literal["observation", "confirmation", "correction"] = "observation"


class MemoryEntry(MemoryContract):
    memory_id: str
    field: str
    value: str
    scope: str
    layer: Literal["short_term", "deep"] = "short_term"
    state: Literal["active", "superseded"] = "active"
    temporary: bool = False
    session_id: str
    independent_sessions: int = 1
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    created_at: str
    updated_at: str
    expires_at: str | None = None
    confirmed_at: str | None = None
