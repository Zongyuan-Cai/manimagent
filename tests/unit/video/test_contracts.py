from __future__ import annotations

import pytest
from pydantic import ValidationError

from manimagent.video.models import (
    BeatDraft,
    Diagram,
    GraphicObject,
    PlanCheck,
    SceneDraft,
)


def test_revision_report_requires_an_actionable_scene():
    with pytest.raises(ValidationError, match="problem_scene_ids"):
        PlanCheck(decision="revise", message="请修复s4的旁白读法", units=[])
    report = PlanCheck(
        decision="revise", message="请修复s4的旁白读法", units=[], problem_scene_ids=["s4"]
    )
    assert report.problem_scene_ids == ["s4"]


@pytest.mark.parametrize("points", [[[0, 0], [0, 0]], [[float("nan"), 0], [1, 1]], [[0, 0]]])
def test_degenerate_or_nonfinite_vectors_are_rejected(points):
    with pytest.raises(ValidationError):
        GraphicObject(object_id="a", kind="vector", points=points)


def test_outside_coordinates_and_arbitrary_code_are_rejected():
    with pytest.raises(ValidationError):
        Diagram(kind="plane", objects=[{"object_id": "p", "kind": "point", "points": [[15, 1]]}])
    with pytest.raises(ValidationError):
        SceneDraft(scene_id="s1", beats=[], python_code="print('unsafe')")


@pytest.mark.parametrize(
    "formula", [r"\input{private}", r"\begin{array}1\end{array}", "$x$", "中文公式"]
)
def test_mathtext_rejects_file_macros_environments_and_non_math_input(formula):
    with pytest.raises(ValidationError):
        BeatDraft(
            beat_id="b1",
            unit_ids=["u001"],
            headline="读取公式",
            screen_text="解释这个公式的计算依据和意义。",
            formulas=[formula],
            spoken_text="先解释这个公式的每个符号，再对应到题目条件。",
            cue_text="先解释",
            reading_reason="理解公式",
        )


def test_mathtext_aliases_and_prompt_examples_keep_command_prefixes():
    from manimagent.video.prompts import OUTLINE_PROMPT, PLAN_PROMPT, SCENE_PROMPT

    slash = chr(92)
    assert BeatDraft.safe_formulas(["frac{1}{2}"]) == [slash + "frac{1}{2}"]
    assert BeatDraft.safe_formulas([slash * 2 + "frac{1}{2}"]) == [slash + "frac{1}{2}"]
    assert BeatDraft.safe_formulas([slash + "mathbf a"]) == [slash + "mathbf{a}"]
    assert BeatDraft.safe_formulas([r"\frac34", r"\frac3{4}", r"\frac{3}4"]) == [r"\frac{3}{4}"] * 3
    assert BeatDraft.safe_formulas([r"\fraction", r"\fracab"]) == [r"\fraction", r"\fracab"]
    assert BeatDraft.safe_formulas([slash + "lVert b" + slash + "rVert"]) == [
        slash + "Vert b" + slash + "Vert"
    ]
    assert slash + "frac{a}{b}" in SCENE_PROMPT and slash + "mathbf{a}" in SCENE_PROMPT
    assert all(
        ord(c) >= 32 or c in "\n\r\t"
        for prompt in [OUTLINE_PROMPT, SCENE_PROMPT, PLAN_PROMPT]
        for c in prompt
    )
