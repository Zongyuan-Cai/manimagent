import pytest

from manimagent.video.notation import break_formula, math_parts, normalize_formula


def test_symbolic_matrix_preserves_order_cells_and_tex_row_separators():
    text = r"A^{-1}=\frac{1}{ad-bc}\begin{pmatrix}d&-b\\-c&a\end{pmatrix}"
    assert normalize_formula(text) == text
    parts = math_parts(text)
    assert parts[0].text == r"A^{-1}=\frac{1}{ad-bc}"
    assert parts[1].rows == (("d", "-b"), ("-c", "a"))
    with pytest.raises(ValueError, match="重复转义"):
        normalize_formula(text.replace("\\", "\\\\"))


@pytest.mark.parametrize(
    "text",
    [
        r"\begin{pmatrix}1&2\\3\end{pmatrix}",
        r"\begin{pmatrix}1&\end{pmatrix}",
        r"\begin{pmatrix}1\end{bmatrix}",
        r"\begin{matrix}1\\2\\3\\4\\5\end{matrix}",
        r"\begin{matrix}\begin{matrix}1\end{matrix}\end{matrix}",
        r"\begin{matrix}\input{secret}\end{matrix}",
        r"\begin{array}1\end{array}",
        r"\begin{cases}a&b&c\end{cases}",
        r"a&b",
        "x\ny",
        "$x$",
        "x=中文",
    ],
)
def test_unsupported_or_ambiguous_notation_is_rejected(text):
    with pytest.raises(ValueError):
        normalize_formula(text)


def test_line_breaks_preserve_statements_and_never_split_matrix_or_fraction():
    assert break_formula(r"A=(0,0)\quad B=(4,0)\quad C=(1,3)") == ["A=(0,0)", "B=(4,0)", "C=(1,3)"]
    assert break_formula(r"y=2x+1=2(3)+1=7") == ["y=2x+1", "=2(3)+1", "=7"]
    for text in [
        r"x=\frac{a=b}{c=d}",
        r"\begin{matrix}a=b&c=d\end{matrix}",
        r"x=2\quad\mathrm{or}\quad x=3",
        r"x=2,y=3",
        r"x=2\mathrm{or}x=3",
    ]:
        assert break_formula(text) == [text]


def test_cases_preserve_equations_and_conditions():
    text = r"f(x)=\begin{cases}x&x\geq0\\-x&x<0\end{cases}"
    assert normalize_formula(text) == text
    assert math_parts(text)[1].rows == (("x", r"x\geq0"), ("-x", "x<0"))


def test_mixed_caption_uses_render_line_width_and_explicit_formula_lines_are_bounded():
    from pydantic import ValidationError

    from manimagent.video.models import BeatDraft, wrap_text

    caption = "因为 b ≠ 0，所以 b·b = ||b||² > 0，可除以 b·b，得到 c = (a·b)/(b·b)，于是 p = c b。"
    data = dict(
        beat_id="b1",
        unit_ids=["u1"],
        headline="系数依据",
        screen_text=caption,
        formulas=["a=1\nb=2"],
        spoken_text="先确认向量非零，再使用点积公式求系数。",
        cue_text="先确认",
        reading_reason="读公式依据",
    )
    beat = BeatDraft.model_validate(data)
    assert beat.screen_text == caption and len(caption) > 64
    assert len(wrap_text(caption).splitlines()) == 2
    assert beat.formulas == ["a=1", "b=2"]
    for updates in [
        dict(screen_text="汉" * 65),
        dict(formulas=["a=1\nb=2\nc=3\nd=4"]),
        dict(formulas=[""]),
    ]:
        with pytest.raises(ValidationError):
            BeatDraft.model_validate({**data, **updates})
