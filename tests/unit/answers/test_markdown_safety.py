from __future__ import annotations

from manimagent.answers.markdown import render_markdown


def test_markdown_html_and_math_are_safe_and_lossless():
    html = render_markdown(
        r"<script>alert(1)</script> $x<y$ [bad](javascript:alert(1))" + "\n\n\\[a=\\frac{1}{2}\\]"
    )
    assert "<script>" not in html and 'href="javascript:' not in html
    assert r"\(x&lt;y\)" in html and r"\[a=\frac{1}{2}\]" in html
    assert "<img" not in render_markdown("![描述](https://remote.example/image.png)")
