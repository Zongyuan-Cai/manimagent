from manimagent.answers.markdown import render_markdown
from manimagent.answers.service import derive_answer


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
