from __future__ import annotations

import pytest

from manimagent.video.models import (
    digest,
)
from manimagent.video.units import teaching_units


def test_source_ranges_preserve_unicode_code_fences_and_display_math():
    markdown = "# 向量😀\n\n已知条件。\n\n```python\nx = 1\n\nprint(x)\n```\n\n\\[\na+b\n\n=c\n\\]\n\n结论。"
    units = teaching_units(markdown)
    assert all(markdown[u.start : u.end] == u.text and digest(u.text) == u.sha256 for u in units)
    assert any("x = 1\n\nprint(x)" in u.text for u in units)
    assert any("a+b\n\n=c" in u.text for u in units)
    assert all(
        c.isspace() or any(u.start <= i < u.end for u in units) for i, c in enumerate(markdown)
    )


def test_equation_paragraphs_do_not_artificially_exceed_unit_limit():
    markdown = "# 逐项推导\n\n" + "\n\n".join(
        f"条件{i}成立。\n\n\\[x_{i}={i}\\]" for i in range(45)
    )
    units = teaching_units(markdown)
    assert 3 <= len(units) <= 70
    assert all(markdown[u.start : u.end] == u.text and digest(u.text) == u.sha256 for u in units)
    assert all(
        c.isspace() or any(u.start <= i < u.end for u in units) for i, c in enumerate(markdown)
    )


@pytest.mark.parametrize(
    "opening, inner, closing",
    [
        ("````markdown", "```python", "````"),
        ("~~~~markdown", "~~~", "~~~~~"),
        ("```python", "``` not a closing fence", "```"),
    ],
)
def test_fence_closes_only_at_its_own_character_length_and_empty_suffix(opening, inner, closing):
    code = f"{opening}\nfirst\n{inner}\n\nsecond\n{closing}\n"
    markdown = f"说明。\n\n{code}\n结论。"
    units = teaching_units(markdown)
    assert len(units) == 3
    assert units[1].text == code
    assert all(markdown[unit.start : unit.end] == unit.text for unit in units)
