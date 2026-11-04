"""Bounded math notation; matrices are parsed as data, never passed to TeX."""

from __future__ import annotations

import re
from dataclasses import dataclass

MATRIX_START = re.compile(r"\\begin\{(pmatrix|bmatrix|vmatrix|matrix|cases)\}")
FORBIDDEN = re.compile(
    r"\\(?:input|include|write|open|read|usepackage|newcommand|def|catcode|special|begin|end)\b"
)


@dataclass(frozen=True)
class MathPart:
    text: str = ""
    environment: str = ""
    rows: tuple[tuple[str, ...], ...] = ()


def scalar(text: str) -> str:
    text = re.sub(r"\\{2,}(?=[A-Za-z{}])", lambda _: "\\", text)
    if FORBIDDEN.search(text) or "&" in text:
        raise ValueError("仅支持 MathText、最多4×4矩阵和最多4行2列的cases，不支持宏或其他环境")
    text = re.sub(
        r"\\(mathbf|mathrm|mathit|mathsf|mathtt|mathcal|mathbb)\s+([A-Za-z0-9])",
        lambda m: "\\" + m[1] + "{" + m[2] + "}",
        text,
    )
    text = re.sub(r"(?<![A-Za-z\\])(?:dfrac|tfrac|frac)(?=\{)", r"\\frac", text)
    for source, target in [
        (r"\lVert", r"\Vert"),
        (r"\rVert", r"\Vert"),
        (r"\lvert", r"\vert"),
        (r"\rvert", r"\vert"),
        (r"\dfrac", r"\frac"),
        (r"\tfrac", r"\frac"),
    ]:
        text = text.replace(source, target)
    # Group TeX's single-token fraction arguments without changing their meaning.
    text = re.sub(
        r"\\frac(?:\s+([A-Za-z0-9])|([0-9]))\s*([A-Za-z0-9])",
        lambda m: "\\frac{" + (m[1] or m[2]) + "}{" + m[3] + "}",
        text,
    )
    text = re.sub(
        r"\\frac(?:\s+([A-Za-z0-9])|([0-9]))(?=\{)",
        lambda m: "\\frac{" + (m[1] or m[2]) + "}",
        text,
    )
    return re.sub(
        r"\\frac(\{[^{}]+\})\s*([A-Za-z0-9])",
        lambda m: "\\frac" + m[1] + "{" + m[2] + "}",
        text,
    )


def math_parts(text: str) -> list[MathPart]:
    """Separate complete inline matrices from scalar MathText, preserving order."""
    parts, offset = [], 0
    for match in MATRIX_START.finditer(text):
        if match.start() < offset:
            raise ValueError("不支持嵌套矩阵")
        if match.start() > offset:
            parts.append(MathPart(text=scalar(text[offset : match.start()])))
        end = text.find(r"\end{" + match[1] + "}", match.end())
        if end < 0:
            raise ValueError("矩阵环境缺少匹配的结束标记")
        body = text[match.end() : end]
        if r"\begin" in body or r"\end" in body:
            raise ValueError("不支持嵌套或错配矩阵")
        rows = tuple(
            tuple(scalar(cell.strip()) for cell in row.split("&")) for row in body.split(r"\\")
        )
        if (
            not 1 <= len(rows) <= 4
            or len({len(row) for row in rows}) != 1
            or any(
                not 1 <= len(row) <= (2 if match[1] == "cases" else 4)
                or any(not cell or len(cell) > 60 for cell in row)
                for row in rows
            )
        ):
            raise ValueError(
                "环境须有1～4行，矩阵1～4列、cases最多2列；单元非空且不超过60字符，各行等宽"
            )
        parts.append(MathPart(environment=match[1], rows=rows))
        offset = end + len(r"\end{" + match[1] + "}")
    if offset < len(text):
        parts.append(MathPart(text=scalar(text[offset:])))
    if sum(bool(p.environment) for p in parts) > 2:
        raise ValueError("一行公式最多两个矩阵，请拆分计算")
    return parts


def normalize_formula(text: str) -> str:
    if "$" in text or not text.isascii() or any(ord(c) < 32 for c in text):
        raise ValueError("公式仅接受可打印 ASCII，不接受中文或 $ 定界符")
    # Unlike scalar commands, matrix row separators carry structural meaning.
    # Do not guess whether a producer escaped the entire expression or just tags.
    if re.search(r"\\{2}begin\{", text):
        raise ValueError("矩阵环境被重复转义；JSON 解码后 begin/end 前仅一个反斜杠，行分隔符两个")
    parts = math_parts(text)
    result = "".join(
        r"\begin{"
        + p.environment
        + "}"
        + r"\\".join("&".join(row) for row in p.rows)
        + r"\end{"
        + p.environment
        + "}"
        if p.environment
        else p.text
        for p in parts
    )
    if not result.strip() or len(result) > 400:
        raise ValueError("公式为空或超过400字符，请拆分")
    return result


def break_formula(text: str) -> list[str]:
    """Wrap independent spaced statements or equality chains, never arithmetic terms."""
    if MATRIX_START.search(text):
        return [text]
    depth, parentheses, cuts, equals = 0, 0, [], []
    independent = bool(re.search(r"\\(?:mathrm\{(?:or|and)\}|lor\b|land\b)", text))
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char in "([":
            parentheses += 1
        elif char in ")]":
            parentheses -= 1
        if depth == 0 and parentheses == 0:
            independent |= char in ",;"
            space = re.match(r"\\(?:qquad|quad)\b", text[index:])
            if space:
                cuts.append((index, index + len(space[0])))
                index += len(space[0])
                continue
            if char == "=":
                equals.append(index)
        index += 1
    if cuts:
        starts = [0] + [end for _, end in cuts]
        ends = [start for start, _ in cuts] + [len(text)]
        lines = [text[a:b].strip() for a, b in zip(starts, ends, strict=True)]
        if all(re.search(r"=|\\(?:neq|perp|to|leq|geq|in)\b", line) for line in lines):
            return lines
        return [text]
    if len(equals) > 1 and not independent:
        cuts = [0, *equals[1:], len(text)]
        return [text[a:b].strip() for a, b in zip(cuts, cuts[1:], strict=False)]
    return [text]
