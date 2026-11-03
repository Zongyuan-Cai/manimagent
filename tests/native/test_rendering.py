"""Opt-in real Manim/FFmpeg tests: RUN_NATIVE_TESTS=1, with the animation extra."""

import os
from itertools import combinations
from types import SimpleNamespace

import pytest

from manimagent.animation import resolve_scene
from manimagent.animation_driver import probe
from manimagent.animation_models import SceneDraft, SceneOutline
from manimagent.animation_renderer import NativeRenderer
from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentError
from manimagent.schemas import Usage

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NATIVE_TESTS") != "1", reason="opt-in native renderer"
)


def planned(identifier, diagrams):
    outline = SceneOutline(
        scene_id=identifier,
        title="完整演示与结果核对",
        objective="解释计算与图形",
        unit_ids=["u1"],
        beat_count=3,
    )
    draft = SceneDraft(
        scene_id=identifier,
        beats=[
            dict(
                beat_id=f"b{i}",
                unit_ids=["u1"],
                headline="核对图形和数值",
                screen_text="先核对题目给定条件，再逐项代入计算。每个矩阵元素保留原有精度，观察图形与公式的对应关系。",
                spoken_text="先核对题目给定条件，再逐项代入计算，观察图形与公式的对应关系。",
                cue_text="先核对",
                reading_reason="读完条件与计算依据",
                formulas=[
                    r"\sigma^2=\frac{1}{5}\sum_{i=1}^{5}(x_i-3)^2"
                    if identifier == "formulas" and i == 0
                    else r"x=\frac{1}{2}"
                ],
                diagram=diagram,
            )
            for i, diagram in enumerate(diagrams)
        ],
    )
    return resolve_scene(outline, draft)


def test_real_render_precision_geometry_and_decoded_complete_movie(tmp_path):
    renderer = NativeRenderer()
    budget = Budget(Settings(runs_dir=tmp_path), Usage())
    near = [[1, 1.0000001], [1, 1.0000002]]
    scenes = [
        planned("matrix", [{"kind": "matrix", "matrix": near}] * 3),
        planned(
            "geometry",
            [
                {
                    "kind": "plane",
                    "objects": [
                        {
                            "object_id": "a",
                            "kind": "vector",
                            "points": [[0, 0], [3, 2]],
                            "label": "a",
                        },
                        {"object_id": "c", "kind": "circle", "points": [[1, 1]], "radius": 0.5},
                    ],
                }
            ]
            * 3,
        ),
        planned("formulas", [{"kind": "none"}] * 3),
    ]
    plan = SimpleNamespace(
        outline=SimpleNamespace(title="真实渲染回归"),
        scenes=scenes,
        duration_seconds=sum(s.duration_seconds for s in scenes),
    )
    renderer.validate_runtime(tmp_path / "check", budget)
    paths = []
    for index, scene in enumerate(scenes):
        directory = tmp_path / scene.scene_id
        if index == 0 and os.name == "nt":
            # The former 64-character formula SVG path exceeded MAX_PATH here.
            directory = directory / ("nested_" + "x" * max(1, 215 - len(str(directory))))
        layout = renderer.validate_layout(
            scene, directory / "layout", plan.outline, len(scenes), budget, index
        )
        result = renderer.render(scene, directory, plan, budget, index)
        assert len(result["frames"]) == 3
        assert all(e["formula_em"] >= 0.468 for e in result["events"])
        if scene.scene_id == "formulas":
            # Stacked limits need more physical height, not smaller body glyphs.
            sizes = [e["formula_bounds"][0] for e in result["events"]]
            assert sizes[0]["top"] - sizes[0]["bottom"] > 1.4
        assert [e["matrix_cells"] for e in layout["events"]] == [
            e["matrix_cells"] for e in result["events"]
        ]
        if index == 0:
            for event in result["events"]:
                cells = event["matrix_cells"]
                assert [float(c["text"]) for c in cells] == [v for row in near for v in row]
                for a, b in combinations([c["bounds"] for c in cells], 2):
                    assert (
                        a["right"] < b["left"]
                        or b["right"] < a["left"]
                        or a["top"] < b["bottom"]
                        or b["top"] < a["bottom"]
                    )
        paths.append(directory / result["video_path"])
    result = renderer.assemble(paths, tmp_path / "full", plan, budget)
    media = probe(tmp_path / "full" / result["video_path"], decode=True)
    assert 120 <= media["duration_seconds"] <= 360
    assert media["decoded_frames"] > 3600
    assert media["audio_streams"] == 0


def test_unreadable_matrix_is_rejected_before_movie_render(tmp_path):
    scene = planned("dense", [{"kind": "matrix", "matrix": [[123456.789123456] * 4] * 4}] * 3)
    with pytest.raises(ContentError, match="可读性"):
        NativeRenderer().validate_layout(
            scene,
            tmp_path,
            SimpleNamespace(title="密集矩阵"),
            1,
            Budget(Settings(runs_dir=tmp_path), Usage()),
            0,
        )
    assert not (tmp_path / "scene.mp4").exists()
