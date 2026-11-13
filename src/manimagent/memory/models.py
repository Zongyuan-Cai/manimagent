"""Finite, sourced personalization storage contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MemoryIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[\w.-]+$"),
]


def valid_scope(value: str) -> str:
    import re

    if not re.fullmatch(r"global|course\.[\w-]+|topic\.[\w-]+\.[\w-]+", value):
        raise ValueError("scope 必须为 global、course.<id> 或 topic.<course>.<id>")
    return value


class MemoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryOptions(MemoryContract):
    session_id: MemoryIdentifier
    scope: str = "global"
    read: bool = True
    learn: bool = True
    _scope = field_validator("scope")(valid_scope)


class MemoryPolicy(MemoryContract):
    promotion_sessions: int = Field(default=3, ge=2, le=20)
    short_term_days: int = Field(default=14, ge=1, le=365)
    max_entries: int = Field(default=200, ge=1, le=10000)
    max_evidence_per_entry: int = Field(default=20, ge=1, le=100)
    max_snapshot_entries: int = Field(default=200, ge=1, le=10000)
    max_snapshot_chars: int = Field(default=100000, ge=256, le=1000000)
    max_sources: int = Field(default=10000, ge=1, le=1000000)


class MemoryEvidence(MemoryContract):
    source_id: str
    session_id: str
    quote: str
    date: str
    kind: Literal["observation", "confirmation", "correction", "restoration"] = "observation"


class MemoryEntry(MemoryContract):
    memory_id: str
    field: str
    value: str
    scope: str
    layer: Literal["short_term", "deep"] = "short_term"
    state: Literal["active", "superseded"] = "active"
    temporary: Literal[False] = False
    session_id: str
    independent_sessions: int = 1
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    created_at: str
    updated_at: str
    expires_at: str | None = None
    confirmed_at: str | None = None
    accepted_sequence: int = 0
    self_reported: bool = False
