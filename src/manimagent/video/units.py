"""Video projections of checked, source-bound answer units; never parse Markdown."""

from __future__ import annotations

from manimagent.errors import ContentError
from manimagent.schemas import Answer
from manimagent.teaching import document_sections, require_verified_document
from manimagent.video.models import TeachingUnit, digest


def teaching_units(answer: Answer) -> list[TeachingUnit]:
    document = require_verified_document(answer)
    units, start = [], 0
    for unit, section in zip(document.units, document_sections(document), strict=True):
        units.append(
            TeachingUnit(
                unit_id=unit.unit_id,
                title=unit.title,
                start=start,
                end=start + len(section),
                text=section,
                sha256=digest(section),
                source_refs=unit.source_refs,
                depends_on=unit.depends_on,
                required=unit.required,
            )
        )
        start += len(section) + 2
    return units


def dependency_closure(units: list[TeachingUnit], unit_ids: list[str]) -> list[TeachingUnit]:
    by_id = {unit.unit_id: unit for unit in units}
    selected, pending = set(), list(unit_ids)
    while pending:
        identifier = pending.pop()
        if identifier not in by_id:
            raise ContentError(
                "invalid_reference", "场景引用了不存在的教学单元", state="needs_review"
            )
        if identifier not in selected:
            selected.add(identifier)
            pending.extend(by_id[identifier].depends_on)
    return [unit for unit in units if unit.unit_id in selected]
