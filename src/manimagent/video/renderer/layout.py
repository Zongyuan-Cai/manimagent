"""Trusted formula and diagram geometry; imported only by the renderer worker."""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from manim import (
    LEFT,
    RIGHT,
    UP,
    Arrow,
    Axes,
    Circle,
    DashedLine,
    Dot,
    Line,
    Polygon,
    SVGMobject,
    Text,
    VGroup,
)
from matplotlib import rc_context
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image

from manimagent.video.notation import math_parts, matrix_text


def font_name(requested: str) -> str:
    import manimpango

    available = set(manimpango.list_fonts())
    choices = (
        [requested]
        if requested != "auto"
        else [
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "SimHei",
            "WenQuanYi Zen Hei",
        ]
    )
    for choice in choices:
        if choice in available:
            return choice
    raise ValueError("未找到中文字体，请安装 Noto Sans CJK SC 或配置 video_font")


class SceneLayout:
    """One render's font, formula cache and current beat's layout diagnostics."""

    def __init__(self, directory: Path, font: str):
        self.font = font
        self.active_beat = "header"
        self.formulas_dir = directory / "formulas"
        self.formulas_dir.mkdir(exist_ok=True)
        self.palette = {
            "blue": "#69A9FF",
            "gold": "#F4CC77",
            "teal": "#62DCC4",
            "red": "#FC8E99",
            "white": "#E9F0FF",
            "muted": "#98ACC7",
        }

    def label(self, text, size=28, color="white"):
        return Text(
            text,
            font=self.font,
            font_size=size,
            color=self.palette.get(color, color),
            line_spacing=0.8,
            use_svg_cache=True,
        )

    def fit(self, mob, width, height, minimum=0.78):
        scale = min(1, width / max(mob.width, 0.001), height / max(mob.height, 0.001))
        if scale < minimum:
            raise ValueError(
                f"layout: 节拍 {self.active_beat} 的内容尺寸 {mob.width:.2f}×{mob.height:.2f} 超过区域 {width}×{height}，缩小后无法保证可读性，请缩短该节拍"
            )
        mob.scale(scale)
        return mob

    def scalar_formula(self, text):
        target = self.formulas_dir / (
            hashlib.sha256(("transparent-2" + text).encode()).hexdigest() + ".svg"
        )
        if not target.exists():
            with rc_context(
                {
                    "savefig.transparent": True,
                    "savefig.facecolor": "none",
                    "figure.facecolor": "none",
                }
            ):
                math_to_image(
                    "$" + text + "$",
                    str(target),
                    prop=FontProperties(size=48),
                    format="svg",
                    color=self.palette["white"],
                )
            tree = ET.parse(target)
            for parent in tree.iter():
                for child in list(parent):
                    if child.get("id") == "patch_1":
                        parent.remove(child)
            tree.write(target, encoding="utf-8", xml_declaration=True)
        # Preserve the font's em scale. Normalizing every formula to the same
        # total height made sums/fractions (with stacked limits) almost illegible.
        svg_height = float(ET.parse(target).getroot().attrib["viewBox"].split()[3])
        return SVGMobject(str(target), height=svg_height * 0.6 / 48)

    def matrix_grid(self, cells, column_gap, left_align=False):
        widths = [max(row[c].width for row in cells) for c in range(len(cells[0]))]
        content = VGroup()
        y = 0
        for row in cells:
            height = max(cell.height for cell in row)
            x = 0
            for width, cell in zip(widths, row, strict=True):
                cell.move_to([x + (cell.width if left_align else width) / 2, -y - height / 2, 0])
                content.add(cell)
                x += width + column_gap
            y += height + 0.24
        return content.move_to([0, 0, 0])

    def formula(self, text):
        parts, cells = VGroup(), []
        for part in math_parts(text):
            if not part.environment:
                value = part.text.strip()
                if not value:
                    continue
                # A superscript following a matrix belongs to that matrix.
                exponent = re.match(r"\^(?:\{([^{}]+)\}|([A-Za-z0-9+-]))", value)
                if exponent and len(parts):
                    power = self.scalar_formula(exponent[1] or exponent[2]).scale(0.65)
                    power.next_to(parts[-1], RIGHT, buff=0.04)
                    power.shift(UP * (parts[-1].get_top()[1] - power.get_center()[1]))
                    base = parts[-1]
                    parts.remove(base)
                    parts.add(VGroup(base, power))
                    value = value[exponent.end() :].strip()
                if value:
                    parts.add(self.scalar_formula(value))
                continue
            grid = [[self.scalar_formula(cell) for cell in row] for row in part.rows]
            content = self.matrix_grid(grid, 0.32, left_align=part.environment == "cases")
            cells.extend(
                (text, cell)
                for texts, row in zip(part.rows, grid, strict=True)
                for text, cell in zip(texts, row, strict=True)
            )
            matrix = VGroup(content)
            if part.environment != "matrix":
                glyphs = {
                    "pmatrix": ("(", ")"),
                    "bmatrix": ("[", "]"),
                    "vmatrix": ("|", "|"),
                    "cases": (r"\{",),
                }
                sides = [LEFT] if part.environment == "cases" else [LEFT, RIGHT]
                for glyph, side in zip(glyphs[part.environment], sides, strict=True):
                    bracket = self.scalar_formula(glyph)
                    bracket.stretch_to_fit_height(content.height + 0.2)
                    bracket.next_to(content, side, buff=0.12)
                    matrix.add(bracket)
            parts.add(matrix)
        parts.arrange(RIGHT, buff=0.16)
        return parts, cells

    def graphic(self, diagram):
        group, objects, arrows = VGroup(), {}, []
        occupied = []
        matrix_cells = []
        if diagram.kind == "plane":
            scale = min(
                5.35 / (diagram.x_range[1] - diagram.x_range[0]),
                3.35 / (diagram.y_range[1] - diagram.y_range[0]),
            )
            axes = Axes(
                x_range=[*diagram.x_range, 1],
                y_range=[*diagram.y_range, 1],
                x_length=(diagram.x_range[1] - diagram.x_range[0]) * scale,
                y_length=(diagram.y_range[1] - diagram.y_range[0]) * scale,
                tips=False,
                axis_config={"color": "#61758D", "stroke_width": 1.6, "include_ticks": True},
            )
            axes.move_to(np.array([-3.45, 0.05, 0]))
            group.add(axes)
            for axis, axis_bounds in [("x", diagram.x_range), ("y", diagram.y_range)]:
                stride = max(1, math.ceil((axis_bounds[1] - axis_bounds[0]) / 7))
                for number in range(
                    math.ceil(axis_bounds[0]), math.floor(axis_bounds[1]) + 1, stride
                ):
                    if not number:
                        continue
                    tick = self.label(str(number), 18, "muted")
                    tick.move_to(
                        axes.c2p(number, 0) + np.array([0, -0.22, 0])
                        if axis == "x"
                        else axes.c2p(0, number) + np.array([-0.22, 0, 0])
                    )
                    group.add(tick)
                    occupied.append(self.bounds(tick))
            for obj in diagram.objects:
                points = [axes.c2p(*p) for p in obj.points]
                color = self.palette[obj.color]
                if obj.kind == "vector":
                    shape = Arrow(
                        points[0],
                        points[1],
                        buff=0,
                        color=color,
                        stroke_width=4,
                        max_tip_length_to_length_ratio=0.13,
                    )
                    arrows.append(shape)
                elif obj.kind == "point":
                    shape = Dot(points[0], radius=0.065, color=color)
                elif obj.kind in {"segment", "dashed"}:
                    shape = (DashedLine if obj.kind == "dashed" else Line)(
                        points[0], points[1], color=color, stroke_width=3
                    )
                elif obj.kind == "polygon":
                    shape = Polygon(*points, color=color, fill_opacity=0.12, stroke_width=3)
                else:
                    radius = abs(
                        axes.c2p(obj.points[0][0] + obj.radius, obj.points[0][1])[0] - points[0][0]
                    )
                    shape = Circle(radius=radius, color=color).move_to(points[0])
                objects[obj.object_id] = shape
                group.add(shape)
                if obj.label:
                    caption = self.label(obj.label, 22, obj.color)
                    placed = False
                    for delta in [0, 0.36, 0.72, -0.55, -0.9]:
                        caption.next_to(shape, UP, buff=0.12).shift([0, delta, 0])
                        caption.shift(
                            np.array(
                                [
                                    max(0, -6.25 - caption.get_left()[0])
                                    - max(0, caption.get_right()[0] + 0.65),
                                    max(0, -1.9 - caption.get_bottom()[1])
                                    - max(0, caption.get_top()[1] - 1.85),
                                    0,
                                ]
                            )
                        )
                        box = self.bounds(caption)
                        if not any(
                            box["left"] < b["right"] + 0.06
                            and box["right"] > b["left"] - 0.06
                            and box["bottom"] < b["top"] + 0.04
                            and box["top"] > b["bottom"] - 0.04
                            for b in occupied
                        ):
                            occupied.append(box)
                            placed = True
                            break
                    if not placed:
                        raise ValueError("layout: 图形标签重叠，请减少同屏对象或缩短标签")
                    group.add(caption)
        elif diagram.kind == "matrix":
            cells = [
                [self.label(matrix_text(value), 38) for value in row] for row in diagram.matrix
            ]
            values = self.matrix_grid(cells, 0.3)
            brackets = VGroup(self.label("[", 100, "blue"), self.label("]", 100, "blue"))
            for bracket, side in zip(brackets, [LEFT, RIGHT], strict=True):
                bracket.stretch_to_fit_height(max(1.3, values.height + 0.2)).next_to(
                    values, side, buff=0.18
                )
            group.add(values, brackets)
            self.fit(group, 5.8, 3.35).move_to(np.array([-3.45, 0, 0]))
            matrix_cells = [
                {"text": matrix_text(value), "bounds": self.bounds(cell)}
                for row, value_row in zip(cells, diagram.matrix, strict=True)
                for cell, value in zip(row, value_row, strict=True)
            ]
        return group, objects, arrows, matrix_cells

    def bounds(self, mob):
        return {
            "left": float(mob.get_left()[0]),
            "right": float(mob.get_right()[0]),
            "bottom": float(mob.get_bottom()[1]),
            "top": float(mob.get_top()[1]),
        }
