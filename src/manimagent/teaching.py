"""Source-bound answer artifacts and deterministic presentation of teaching units."""

from __future__ import annotations

import hashlib
import json

from manimagent.errors import ContentError
from manimagent.schemas import Answer, AnswerDocument, AnswerStep, SourceBundle


def artifact_digest(artifact) -> str:
    value = artifact.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_sources(document: AnswerDocument, sources: SourceBundle) -> None:
    allowed = {(ref.material_id, ref.block_id) for ref in sources.allowed_source_refs}
    for unit in document.units:
        if any((ref.material_id, ref.block_id) not in allowed for ref in unit.source_refs):
            raise ContentError(
                "invalid_reference", "教学单元包含未提供的材料来源", state="needs_review"
            )


def document_sections(document: AnswerDocument) -> list[str]:
    if document.origin == "legacy_markdown":
        return [document.units[0].content]
    sections = []
    for index, unit in enumerate(document.units):
        prefix = f"# {document.title}\n\n" if index == 0 else ""
        refs = " ".join(f"[[{ref.material_id}:{ref.block_id}]]" for ref in unit.source_refs)
        sections.append(f"{prefix}## {unit.title}\n\n{unit.content}\n\n{refs}")
    return sections


def render_document(document: AnswerDocument) -> str:
    return "\n\n".join(document_sections(document))


def document_steps(document: AnswerDocument) -> list[AnswerStep]:
    return [
        AnswerStep(
            step_id=unit.unit_id,
            title=unit.title,
            content=unit.content,
            source_refs=list(unit.source_refs),
        )
        for unit in document.units
    ]


def require_verified_document(answer: Answer) -> AnswerDocument:
    document, receipt = answer.document, answer.verification
    if document is None or receipt is None:
        raise ContentError(
            "content_uncertain", "视频需要带有效内容检查凭据的教学文档", state="needs_review"
        )
    if (
        receipt.answer_id != answer.answer_id
        or receipt.document_digest != artifact_digest(document)
        or render_document(document) != answer.markdown
    ):
        raise ContentError(
            "content_uncertain", "正文、教学单元或检查凭据已变化，请重新检查", state="needs_review"
        )
    return document
