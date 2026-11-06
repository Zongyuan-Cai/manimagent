"""Conservative OOXML math conversion; unknown structures never become flattened text.

OMML object structure follows Microsoft's OfficeMath documentation:
https://devblogs.microsoft.com/math-in-office/officemath/
"""

from __future__ import annotations

from docx.oxml.ns import qn


class UnsupportedMath(ValueError):
    pass


def _name(node):
    return node.tag.rsplit("}", 1)[-1]


def _escape(value):
    mapping = {
        "\\": r"\backslash ",
        "{": r"\{",
        "}": r"\}",
        "_": r"\_",
        "%": r"\%",
        "#": r"\#",
        "&": r"\&",
        "$": r"\$",
        "^": r"\hat{}",
        "~": r"\sim ",
        "−": "-",
        "×": r"\times ",
        "÷": r"\div ",
        "≤": r"\le ",
        "≥": r"\ge ",
        "≠": r"\ne ",
        "∞": r"\infty ",
        "∈": r"\in ",
        "→": r"\to ",
    }
    return "".join(mapping.get(char, char) for char in value)


def _property(node, container, name, default=None):
    prop = node.find(qn("m:" + container))
    element = prop.find(qn("m:" + name)) if prop is not None else None
    return element.get(qn("m:val"), "") if element is not None else default


def _children(node, allowed):
    for child in node:
        if child.tag not in {qn("m:" + name) for name in allowed}:
            raise UnsupportedMath("包含未支持的公式结构 " + _name(child))


def omml_to_latex(node, depth=0):
    if depth > 40:
        raise UnsupportedMath("公式嵌套过深")
    name = _name(node)
    if not node.tag.startswith(qn("m:t").rsplit("}", 1)[0] + "}"):
        raise UnsupportedMath("公式包含非数学对象")

    def part(tag, optional=False):
        values = node.findall(qn("m:" + tag))
        if len(values) != 1:
            if optional and not values:
                return ""
            raise UnsupportedMath("公式缺少或重复 " + tag)
        result = omml_to_latex(values[0], depth + 1)
        if not result and not optional:
            raise UnsupportedMath("公式包含空参数 " + tag)
        return result

    if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "lim", "fName"}:
        result = ""
        for child in node:
            if child.tag in {qn("m:argPr"), qn("m:oMathParaPr")}:
                continue  # Argument size and paragraph alignment do not change the expression.
            result += omml_to_latex(child, depth + 1)
        if name in {"oMath", "oMathPara"} and not result:
            raise UnsupportedMath("公式对象为空")
        return result
    if name == "t":
        return _escape(node.text or "")
    if name == "r":
        # Word run properties are handled conservatively; symbols/objects cannot disappear.
        for child in node:
            if child.tag not in {qn("m:t"), qn("m:rPr"), qn("w:rPr")}:
                raise UnsupportedMath("公式文字包含特殊对象")
        text = "".join(_escape(child.text or "") for child in node if child.tag == qn("m:t"))
        script = _property(node, "rPr", "scr", "roman")
        style = _property(node, "rPr", "sty", "i")
        wrappers = {
            "roman": None,
            "double-struck": "mathbb",
            "fraktur": "mathfrak",
            "script": "mathcal",
            "sans-serif": "mathsf",
            "monospace": "mathtt",
        }
        if script not in wrappers or style not in {"p", "i", "b", "bi"}:
            raise UnsupportedMath("公式字形样式无法可靠转换")
        wrapper = wrappers[script]
        if wrapper:
            text = "\\" + wrapper + "{" + text + "}"
        if style in {"b", "bi"}:
            text = r"\boldsymbol{" + text + "}"
        elif style == "p" and not wrapper:
            text = r"\mathrm{" + text + "}"
        if _property(node, "rPr", "nor", "0") in {"", "1", "true", "on"}:
            text = r"\text{" + text + "}"
        return text
    if name == "f":
        _children(node, {"fPr", "num", "den"})
        numerator, denominator = part("num"), part("den")
        kind = _property(node, "fPr", "type", "bar")
        if kind == "noBar":
            return r"\genfrac{}{}{0pt}{}{" + numerator + "}{" + denominator + "}"
        if kind not in {"bar", "skw", "lin"}:
            raise UnsupportedMath("分数类型无法可靠转换")
        return r"\frac{" + numerator + "}{" + denominator + "}"
    if name in {"sSup", "sSub", "sSubSup"}:
        _children(node, {name + "Pr", "e", "sub", "sup"})
        base = "{" + part("e") + "}"
        if name in {"sSub", "sSubSup"}:
            base += "_{" + part("sub") + "}"
        if name in {"sSup", "sSubSup"}:
            base += "^{" + part("sup") + "}"
        return base
    if name == "rad":
        _children(node, {"radPr", "e", "deg"})
        degree = part("deg", optional=True)
        if _property(node, "radPr", "degHide", "0") in {"", "1", "true", "on"}:
            degree = ""
        return r"\sqrt" + ("[" + degree + "]" if degree else "") + "{" + part("e") + "}"
    if name == "d":
        _children(node, {"dPr", "e"})
        delimiters = {
            "(": "(",
            ")": ")",
            "[": "[",
            "]": "]",
            "{": r"\{",
            "}": r"\}",
            "|": "|",
            "‖": r"\Vert",
            "⟨": r"\langle",
            "⟩": r"\rangle",
            "": ".",
        }
        begin = _property(node, "dPr", "begChr", "(")
        end = _property(node, "dPr", "endChr", ")")
        if begin not in delimiters or end not in delimiters:
            raise UnsupportedMath("公式括号无法可靠转换")
        elements = node.findall(qn("m:e"))
        if not elements:
            raise UnsupportedMath("括号内没有公式")
        separator = _escape(_property(node, "dPr", "sepChr", "|"))
        return (
            r"\left"
            + delimiters[begin]
            + " "
            + separator.join(omml_to_latex(item, depth + 1) for item in elements)
            + r"\right"
            + delimiters[end]
            + " "
        )
    if name == "nary":
        _children(node, {"naryPr", "e", "sub", "sup"})
        operators = {
            "∑": r"\sum",
            "∏": r"\prod",
            "∫": r"\int",
            "∬": r"\iint",
            "∭": r"\iiint",
            "∮": r"\oint",
        }
        operator = _property(node, "naryPr", "chr")
        if operator not in operators:
            raise UnsupportedMath("求和或积分符号未明确或未支持")
        result = operators[operator]
        for field, marker in (("sub", "_"), ("sup", "^")):
            value = part(field, optional=True)
            hidden = _property(node, "naryPr", field + "Hide", "0") in {"", "1", "true", "on"}
            if value and hidden:
                raise UnsupportedMath("求和或积分包含隐藏的上下限")
            if value:
                result += marker + "{" + value + "}"
        return result + " " + part("e")
    if name == "m":
        _children(node, {"mPr", "mr"})
        rows = []
        for row in node.findall(qn("m:mr")):
            _children(row, {"e"})
            rows.append([omml_to_latex(cell, depth + 1) for cell in row])
        if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
            raise UnsupportedMath("矩阵为空或列数不一致")
        return r"\begin{matrix}" + r" \\ ".join(" & ".join(row) for row in rows) + r"\end{matrix}"
    if name == "box":
        _children(node, {"boxPr", "e"})
        return part("e")
    if name == "func":
        _children(node, {"funcPr", "fName", "e"})
        return part("fName") + r"\," + part("e")
    raise UnsupportedMath("未支持的公式对象 " + name)


def paragraph_text(paragraph, drawings: dict | None = None) -> tuple[str, list[str]]:
    """Read runs and inline OMML in order, preserving explicit super/subscript groups."""
    segments = []
    issues = []

    def append(kind, value):
        if segments and segments[-1][0] == kind:
            segments[-1] = (kind, segments[-1][1] + (" " if kind == "math" else "") + value)
        else:
            segments.append((kind, value))

    def visit(node):
        if node.tag == qn("w:r"):
            text = ""
            for child in node:
                if child.tag == qn("w:t"):
                    text += child.text or ""
                elif child.tag in {qn("w:br"), qn("w:cr")}:
                    text += "\n"
                elif child.tag == qn("w:tab"):
                    text += "\t"
                elif child.tag == qn("w:noBreakHyphen"):
                    text += "-"
                elif child.tag == qn("w:drawing") and child in (drawings or {}):
                    text += drawings[child]
                elif child.tag not in {qn("w:rPr"), qn("w:lastRenderedPageBreak")}:
                    issues.append("DOCX 文字包含无法可靠读取的对象 " + _name(child))
            align = node.find("./" + qn("w:rPr") + "/" + qn("w:vertAlign"))
            kind = align.get(qn("w:val"), "baseline") if align is not None else "baseline"
            if kind not in {"baseline", "superscript", "subscript"}:
                issues.append("DOCX 上下标格式无法可靠读取")
            append(kind, text)
        elif node.tag in {qn("m:oMath"), qn("m:oMathPara")}:
            try:
                append("math", "$" + omml_to_latex(node) + "$")
            except UnsupportedMath as exc:
                issues.append("DOCX " + str(exc) + "，请提供明确 LaTeX 或核对原件")
        elif node.tag == qn("w:hyperlink"):
            for child in node:
                visit(child)
        elif node.tag not in {
            qn("w:pPr"),
            qn("w:bookmarkStart"),
            qn("w:bookmarkEnd"),
            qn("w:proofErr"),
        }:
            issues.append("DOCX 段落包含无法可靠读取的对象 " + _name(node))

    for child in paragraph._p:
        visit(child)
    text = ""
    for kind, value in segments:
        if kind in {"superscript", "subscript"}:
            text += ("^" if kind == "superscript" else "_") + "{" + value + "}"
        else:
            text += value
    return text, issues
