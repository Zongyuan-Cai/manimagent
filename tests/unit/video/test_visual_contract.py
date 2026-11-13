"""Visual settings change executable parameters; graph meaning is not guessed."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from manimagent.errors import ContentError
from manimagent.personalization import EffectiveProfile, VisualSpec
from manimagent.video.capabilities import (
    function_line,
    geometry_points,
    polynomial_bounds,
    visual_parameters,
)
from manimagent.video.models import Diagram, GraphicObject
from manimagent.video.renderer.process import NativeRenderer
from manimagent.video.timeline import build_plan


def test_discrete_samples_are_not_accepted_as_a_curve_or_secant():
    samples = GraphicObject(object_id="n", kind="samples", points=[(0, 0), (1, 1), (2, 4)])
    assert geometry_points(samples) == [(0, 0), (1, 1), (2, 4)]
    for kind in ("function", "secant", "tangent"):
        with pytest.raises(ValidationError, match="不接受手填采样点"):
            GraphicObject(object_id="curve", kind=kind, points=samples.points)


def test_polynomial_curve_and_derivative_determine_geometry():
    curve = GraphicObject(object_id="f", kind="function", coefficients=[0, 0, 1], domain=(-2, 2))
    points = geometry_points(curve)
    assert len(points) == 257
    assert points[0] == (-2, 4) and points[128] == (0, 0) and points[-1] == (2, 4)
    tangent = GraphicObject(
        object_id="t", kind="tangent", coefficients=[0, 0, 1], domain=(0, 2), at=1
    )
    secant = GraphicObject(
        object_id="s", kind="secant", coefficients=[0, 0, 1], domain=(0, 2), at=0, through=2
    )
    assert function_line(tangent) == (2, -1)
    assert geometry_points(tangent) == [(0, -1), (2, 3)]
    assert function_line(secant) == (2, 0)
    with pytest.raises(ValidationError, match="不同横坐标"):
        GraphicObject(
            object_id="s", kind="secant", coefficients=[0, 0, 1], domain=(0, 2), at=1, through=1
        )
    with pytest.raises(ValidationError):
        GraphicObject(object_id="arbitrary", kind="function", expression="sin(x)", domain=(0, 2))


def test_internal_extrema_and_circle_boundaries_cannot_be_clipped_silently():
    # Endpoints are both zero; the interior maximum is 2.25.
    assert polynomial_bounds([0, 3, -1], (0, 3)) == pytest.approx((0, 2.25))
    with pytest.raises(ValidationError):
        Diagram(
            kind="plane",
            x_range=(0, 3),
            y_range=(0, 2),
            objects=[
                {"object_id": "f", "kind": "function", "coefficients": [0, 3, -1], "domain": [0, 3]}
            ],
        )
    with pytest.raises(ValidationError, match="圆的边界"):
        Diagram(
            kind="plane",
            x_range=(0, 3),
            y_range=(0, 3),
            objects=[{"object_id": "c", "kind": "circle", "points": [[0, 1]], "radius": 1}],
        )


def test_themes_and_text_size_are_executable_and_raw_colors_rejected():
    standard = visual_parameters(VisualSpec())
    chosen = visual_parameters(VisualSpec(theme="blue_gold", font_scale="large", motion="minimal"))
    assert chosen["palette"]["accent"] == "#FFD166"
    assert chosen["palette"] != standard["palette"]
    assert chosen["font_multiplier"] > standard["font_multiplier"]
    assert chosen["motion"] == "minimal"
    with pytest.raises(ValidationError):
        GraphicObject(object_id="p", kind="point", points=[[0, 0]], color="#FFFFFF")


def test_numberline_requires_one_dimensional_geometry():
    Diagram(kind="numberline", objects=[{"object_id": "p", "kind": "point", "points": [[1, 0]]}])
    with pytest.raises(ValidationError, match="数轴"):
        Diagram(
            kind="numberline", objects=[{"object_id": "p", "kind": "point", "points": [[1, 1]]}]
        )


def test_renderer_evidence_must_match_applied_color_and_frozen_parameters():
    obj = GraphicObject(object_id="p", kind="point", points=[[0, 0]], color="accent")
    spec = VisualSpec(theme="blue_gold")
    scene = SimpleNamespace(
        visual_spec=spec, beats=[SimpleNamespace(diagram=Diagram(kind="plane", objects=[obj]))]
    )
    parameters = visual_parameters(spec)
    event = {
        "visual_parameters": parameters,
        "object_styles": [{"object_id": "p", "color_role": "accent", "color": "#FFD166"}],
    }
    result = {"visual_parameters": parameters, "events": [event]}
    NativeRenderer.verify_visual(result, scene)
    event["object_styles"][0]["color"] = "#FFFFFF"
    with pytest.raises(ContentError, match="实际图形颜色"):
        NativeRenderer.verify_visual(result, scene)


def test_scene_cannot_override_the_accepted_answer_visual_profile():
    answer = SimpleNamespace(
        effective_profile=EffectiveProfile(visual=VisualSpec(theme="blue_gold")).model_dump()
    )
    scene = SimpleNamespace(visual_spec=VisualSpec())
    with pytest.raises(ContentError, match="冻结视觉参数"):
        build_plan(answer, None, [], [scene])
