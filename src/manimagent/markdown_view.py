"""Safe Markdown HTML with math delimiters retained for the local MathJax renderer."""

import re
from html import escape

from markdown_it import MarkdownIt
from mdit_py_plugins.texmath import texmath_plugin


def render_markdown(text: str) -> str:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    md.use(texmath_plugin, delimiters="dollars")
    md.use(texmath_plugin, delimiters="brackets")

    def math_boundary(state, start, _end, silent):
        # texmath's default block rules do not interrupt a preceding paragraph.
        # A writer commonly emits "Explanation\n\\[...\\]" without a blank line.
        line = state.src[state.bMarks[start] + state.tShift[start] : state.eMarks[start]]
        return silent and line.startswith((r"\[", "$$"))

    md.block.ruler.before("fence", "math_boundary", math_boundary, {"alt": ["paragraph"]})

    def cjk_strong(state, silent):
        match = re.match(
            r"\*\*([^\n*]+[：，。！？；:])\*\*(?=[\u3400-\u9fff])", state.src[state.pos :]
        )
        if not match:
            return False
        if not silent:
            state.push("strong_open", "strong", 1)
            token = state.push("text", "", 0)
            token.content = match[1]
            state.push("strong_close", "strong", -1)
        state.pos += len(match[0])
        return True

    md.inline.ruler.before("emphasis", "cjk_strong", cjk_strong)

    def image(self, tokens, idx, options, env):
        return "[图片：" + escape(tokens[idx].content) + "]"

    md.add_render_rule("image", image)

    def inline(self, tokens, idx, options, env):
        return '<span class="math">\\(' + escape(tokens[idx].content) + "\\)</span>"

    def block(self, tokens, idx, options, env):
        return '<div class="math">\\[' + escape(tokens[idx].content) + "\\]</div>\n"

    for name in ("math_inline", "math_single"):
        md.add_render_rule(name, inline)
    for name in ("math_block", "math_block_eqno"):
        md.add_render_rule(name, block)
    sources = {}

    def citation(match):
        marker = match.group(1)
        sources.setdefault(marker, len(sources) + 1)
        return f"[来源 {sources[marker]}](#{marker.split(':')[1]})"

    text = re.sub(r"\[\[((?:material_[a-f0-9]{32}):(?:block_[a-f0-9]{32}))\]\]", citation, text)
    return md.render(text)
