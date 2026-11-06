import pytest

from manimagent.answers.markdown import render_markdown
from manimagent.answers.service import derive_answer
from manimagent.errors import ContentError


def test_markdown_title_ignores_hash_characters_inside_fenced_code():
    material = "material_" + "a" * 32
    block = "block_" + "b" * 32
    markdown = (
        f"```python\n# this is a comment\n```\n\n## Actual title\nAnswer [[{material}:{block}]]"
    )
    answer = derive_answer(
        markdown,
        [material],
        "question",
        {"allowed_source_refs": [{"material_id": material, "block_id": block}]},
    )
    assert answer.title == "Actual title" and answer.markdown == markdown


@pytest.mark.parametrize(
    "reference",
    [
        "material_id:{material}:{block}",
        "material_id:{material}:block_id:{block}",
        "{material}:block_id:{block}",
        " {material}:{block}",
    ],
)
def test_field_labels_are_not_silently_removed_from_invalid_source_references(reference):
    material, block = "material_" + "a" * 32, "block_" + "b" * 32
    marker = reference.format(material=material, block=block)
    with pytest.raises(ContentError) as error:
        derive_answer(
            f"结果为2 [[{marker}]]",
            [material],
            "question",
            {"allowed_source_refs": [{"material_id": material, "block_id": block}]},
        )
    assert error.value.error.code == "invalid_reference" and error.value.state == "needs_review"


def test_real_model_display_math_without_blank_lines_keeps_latex_delimiters():
    markdown = (
        "已知  \n\\[\na=(3,2),\\qquad b=(2,0).\n\\]\n计算结果如下。\n\n所以\n$$c=\\frac32$$\n结论。"
    )
    html = render_markdown(markdown)
    assert html.count('class="math"') == 2
    assert r"\[" in html and r"\frac32" in html
    assert "计算结果如下" in html and "结论" in html


def test_cjk_bold_punctuation_renders_without_changing_code_blocks():
    html = render_markdown("**来源引用说明：**题目来自材料。\n\n```\n**说明：**代码\n```")
    assert "<strong>来源引用说明：</strong>题目" in html
    assert "**说明：**代码" in html
