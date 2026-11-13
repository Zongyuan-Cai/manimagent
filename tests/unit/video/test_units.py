from __future__ import annotations

import pytest
from pydantic import ValidationError

from manimagent.errors import ContentError
from manimagent.schemas import (
    Answer,
    AnswerCheck,
    AnswerDocument,
    AnswerUnit,
    SourceRef,
    VerifiedAnswer,
)
from manimagent.teaching import artifact_digest, document_steps, render_document
from manimagent.video.models import digest
from manimagent.video.units import dependency_closure, teaching_units

REF = SourceRef(material_id="material_" + "a" * 32, block_id="block_" + "b" * 32)


def checked_answer():
    document = AnswerDocument(
        title="向量😀",
        units=(
            AnswerUnit(
                unit_id="conditions",
                title="条件",
                content="已知条件。\n\n```python\nx = 1\n\nprint(x)\n```",
                source_refs=(REF,),
                depends_on=(),
                required=True,
            ),
            AnswerUnit(
                unit_id="reason",
                title="推理",
                content="\\[\na+b\n\n=c\n\\]",
                source_refs=(REF,),
                depends_on=("conditions",),
                required=True,
            ),
            AnswerUnit(
                unit_id="conclusion",
                title="结论",
                content="结论。",
                source_refs=(REF,),
                depends_on=("reason",),
                required=True,
            ),
            AnswerUnit(
                unit_id="example",
                title="补充例子",
                content="例如。",
                source_refs=(REF,),
                depends_on=("conditions",),
                required=False,
            ),
        ),
    )
    answer = Answer(
        answer_id="answer_" + "c" * 32,
        material_ids=[REF.material_id],
        title=document.title,
        instruction="完整解答",
        markdown=render_document(document),
        steps=document_steps(document),
        document=document,
    )
    answer.verification = VerifiedAnswer(
        answer_id=answer.answer_id,
        document_digest=artifact_digest(document),
        source_digest="0" * 64,
        policy_hash="1" * 64,
        checker_profile_hash="2" * 64,
        report=AnswerCheck(decision="pass", message="已核对"),
    )
    return answer


def test_video_units_are_exact_projections_of_the_checked_document_including_unicode_math_and_fences():
    answer = checked_answer()
    units = teaching_units(answer)
    assert [unit.unit_id for unit in units] == [unit.unit_id for unit in answer.document.units]
    assert all(
        answer.markdown[u.start : u.end] == u.text and digest(u.text) == u.sha256 for u in units
    )
    assert "x = 1\n\nprint(x)" in units[0].text
    assert "a+b\n\n=c" in units[1].text
    assert units[2].depends_on == ("reason",)
    assert units[3].required is False
    assert all(
        c.isspace() or any(u.start <= i < u.end for u in units)
        for i, c in enumerate(answer.markdown)
    )


def test_scene_context_gets_transitive_dependencies_in_document_order():
    units = teaching_units(checked_answer())
    assert [unit.unit_id for unit in dependency_closure(units, ["conclusion"])] == [
        "conditions",
        "reason",
        "conclusion",
    ]
    with pytest.raises(ContentError, match="不存在"):
        dependency_closure(units, ["invented"])


@pytest.mark.parametrize("change", ["missing", "content", "markdown", "wrong_answer"])
def test_unchecked_or_changed_document_cannot_reach_video_units(change):
    answer = checked_answer()
    if change == "missing":
        answer.verification = None
    elif change == "content":
        answer.document = answer.document.model_copy(update={"title": "改过的标题"})
    elif change == "markdown":
        answer.markdown += "\n未检查的新增结论"
    else:
        answer.answer_id = "answer_" + "d" * 32
    with pytest.raises(ContentError):
        teaching_units(answer)


def test_structured_units_and_source_refs_are_immutable():
    answer = checked_answer()
    with pytest.raises(ValidationError):
        answer.document.units[0].content = "悄悄替换内容"
    with pytest.raises(ValidationError):
        answer.document.units[0].source_refs[0].block_id = "block_" + "d" * 32


def test_generated_titles_cannot_bypass_source_validation_with_embedded_markers():
    candidate = checked_answer().document.model_dump(mode="json")
    candidate["title"] = "未绑定引用 [[invented:source]]"
    with pytest.raises(ValidationError):
        AnswerDocument.model_validate(candidate)


@pytest.mark.parametrize(
    "bad_units",
    [
        [{"unit_id": "same", "depends_on": []}, {"unit_id": "same", "depends_on": []}],
        [{"unit_id": "a", "depends_on": ["missing"]}],
        [{"unit_id": "a", "depends_on": ["b"]}, {"unit_id": "b", "depends_on": ["a"]}],
        [{"unit_id": "a", "depends_on": ["a"]}],
    ],
)
def test_duplicate_missing_and_circular_unit_dependencies_are_rejected(bad_units):
    with pytest.raises(ValidationError):
        AnswerDocument(
            title="解答",
            units=tuple(
                AnswerUnit(title="步骤", content="正文", source_refs=(REF,), required=True, **unit)
                for unit in bad_units
            ),
        )
