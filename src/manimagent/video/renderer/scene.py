"""Trusted scene playback and timing evidence, using one shared geometry implementation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from manim import (
    LEFT,
    UP,
    FadeIn,
    FadeOut,
    GrowArrow,
    Indicate,
    Line,
    RoundedRectangle,
    Scene,
    VGroup,
    tempconfig,
)

from manimagent.video.models import PlannedScene
from manimagent.video.models import wrap_text as wrapped
from manimagent.video.notation import break_formula
from manimagent.video.renderer.layout import SceneLayout, font_name
from manimagent.video.renderer.media import extract, probe


def render(data: dict, directory: Path) -> dict:
    spec = PlannedScene.model_validate(data["scene"])
    font = font_name(data.get("font", "auto"))
    layout = SceneLayout(directory, font)
    evidence = []

    class TeachingScene(Scene):
        def play(self, *animations, **kwargs):
            if data.get("layout_only"):
                # Geometry is already constructed. Preflight does not need animation
                # interpolation/deep copies; the real render validates event timing.
                self.renderer.time += kwargs.get(
                    "run_time", max((a.run_time for a in animations), default=1)
                )
            else:
                super().play(*animations, **kwargs)

        def wait(self, duration=1, **kwargs):
            if data.get("layout_only"):
                self.renderer.time += duration
            else:
                super().wait(duration, **kwargs)

        def construct(self):
            header = layout.fit(layout.label(data["title"], 26), 10.5, 0.5).move_to(
                np.array([-6.5, 3.48, 0]), aligned_edge=LEFT
            )
            count = layout.label(
                f"{data['scene_index'] + 1:02d} / {data['scene_count']:02d}", 22, "muted"
            ).move_to(np.array([5.85, 3.48, 0]))
            divider = Line([-6.65, 3.05, 0], [6.65, 3.05, 0], color="#33465F", stroke_width=1)
            scene_title = layout.fit(layout.label(spec.title, 24, "blue"), 12.5, 0.45).move_to(
                np.array([-6.5, 2.65, 0]), aligned_edge=LEFT
            )
            caption_card = RoundedRectangle(
                width=13.1,
                height=1.32,
                corner_radius=0.15,
                stroke_width=0,
                fill_color="#18283D",
                fill_opacity=1,
            ).move_to([0, -2.83, 0])
            self.add(header, count, divider, scene_title, caption_card)
            previous = VGroup()
            for index, beat in enumerate(spec.beats):
                layout.active_beat = beat.beat_id
                start = float(self.time)
                if len(previous):
                    self.play(FadeOut(previous), run_time=0.25)
                else:
                    self.wait(0.25, frozen_frame=True)
                diagram, objects, arrows, matrix_cells = layout.graphic(beat.diagram)
                has_graphic = beat.diagram.kind != "none"
                center = np.array([3.35 if has_graphic else 0, 0.2, 0])
                width = 6.05 if has_graphic else 11.7
                title = layout.fit(layout.label(beat.headline, 29, "gold"), width, 0.55).move_to(
                    center + [0, 1.55, 0]
                )
                equations, inline_cells, formula_lines = VGroup(), [], []
                for text in beat.formulas:
                    equation, cells = layout.formula(text)
                    lines = break_formula(text) if equation.width * 0.78 > width else [text]
                    if len(lines) == 1:
                        equations.add(equation)
                        inline_cells.extend(cells)
                    else:
                        for line in lines:
                            equation, cells = layout.formula(line)
                            equations.add(equation)
                            inline_cells.extend(cells)
                    formula_lines.extend(lines)
                formula_em = None
                if len(equations):
                    equations.arrange(-UP, buff=0.32)
                    original_height = equations.height
                    layout.fit(equations, width, 2.3).move_to(center)
                    formula_em = round(0.6 * equations.height / original_height, 6)
                else:
                    equations = layout.fit(
                        layout.label(wrapped(beat.screen_text, 18 if has_graphic else 34), 30),
                        width,
                        2.2,
                    ).move_to(center)
                caption = layout.fit(
                    layout.label(wrapped(beat.screen_text), 28), 12.4, 1.0
                ).move_to([0, -2.83, 0])
                page = layout.label(
                    f"{index + 1} / {len(spec.beats)}  ·  教学讲解", 17, "muted"
                ).move_to([5.4, -3.76, 0])
                content = VGroup(title, equations, caption, page, diagram)
                for part in [title, equations, caption, diagram]:
                    b = layout.bounds(part)
                    if (
                        b["left"] < -6.7
                        or b["right"] > 6.7
                        or b["bottom"] < -3.53
                        or b["top"] > 2.25
                    ):
                        raise ValueError("layout: 内容越出安全边距")
                self.play(FadeIn(VGroup(title, equations, caption, page)), run_time=0.35)
                # Draw vectors; the rest of the diagram appears in the same fixed window.
                others = VGroup(*(part for part in diagram if all(part is not a for a in arrows)))
                animations = [FadeIn(others)] if len(others) else []
                animations.extend(GrowArrow(a) for a in arrows)
                if animations:
                    self.play(*animations, run_time=0.4)
                else:
                    self.wait(0.4, frozen_frame=True)
                stable = float(self.time)
                cue = max(stable, start + beat.cue_seconds - beat.start_seconds)
                if cue > self.time:
                    self.wait(cue - self.time, frozen_frame=True)
                highlight = (
                    VGroup(*(objects[identifier] for identifier in beat.cue_objects))
                    if beat.cue_objects
                    else equations
                )
                self.play(
                    Indicate(highlight, scale_factor=1, color=layout.palette["gold"]), run_time=0.5
                )
                # Frozen waits floor duration / frame_duration in Manim. Count
                # frames against the absolute plan to prevent per-beat drift.
                remaining_frames = round(beat.end_seconds * 30) - round(float(self.time) * 30)
                if remaining_frames < 0:
                    raise ValueError("timing: 动作超过分配时间")
                if remaining_frames:
                    self.wait((remaining_frames + 0.001) / 30, frozen_frame=True)
                evidence.append(
                    {
                        "beat_id": beat.beat_id,
                        "unit_ids": beat.unit_ids,
                        "start_seconds": start,
                        "stable_seconds": stable,
                        "cue_seconds": cue,
                        "end_seconds": float(self.time),
                        "screen_text": beat.screen_text,
                        "formulas": beat.formulas,
                        "formula_lines": formula_lines,
                        "formula_em": formula_em,
                        "formula_bounds": [layout.bounds(e) for e in equations]
                        if beat.formulas
                        else [],
                        "inline_matrix_cells": [
                            {"text": text, "bounds": layout.bounds(cell)}
                            for text, cell in inline_cells
                        ],
                        "objects": list(objects),
                        "matrix_cells": matrix_cells,
                        "caption_bounds": layout.bounds(caption),
                        "content_bounds": layout.bounds(VGroup(title, equations, diagram)),
                        "time_source": beat.time_source,
                        "cue_time_source": beat.cue_time_source,
                        "frame_seconds": min(float(self.time) - 0.2, stable + 0.6),
                    }
                )
                previous = content
                self.add(content)

    with tempconfig(
        {
            "renderer": "cairo",
            "pixel_width": 1280,
            "pixel_height": 720,
            "frame_rate": 30,
            "background_color": "#0C1728",
            "media_dir": str(directory / "media"),
            "video_dir": str(directory),
            "output_file": "scene.mp4",
            "format": "mp4",
            "verbosity": "ERROR",
            "progress_bar": "none",
            "disable_caching": False,
            "max_files_cached": 200,
            "write_to_movie": True,
            "dry_run": data.get("layout_only", False),
            "skip_animations": data.get("layout_only", False),
        }
    ):
        scene = TeachingScene()
        scene.render()
    if data.get("layout_only"):
        return {"status": "success", "font": font, "events": evidence, "layout_only": True}
    path = directory / "scene.mp4"
    metadata = probe(path)
    frames = extract(path, [e["frame_seconds"] for e in evidence], directory)
    return {
        "status": "success",
        "video_path": path.name,
        "media": metadata,
        "events": evidence,
        "frames": frames,
        "font": font,
    }
