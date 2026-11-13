"""Finite renderer capabilities, semantic colours and safe polynomial geometry."""

from __future__ import annotations

CAPABILITY_VERSION = "teaching-visual-2"
CAPABILITIES = {
    "version": CAPABILITY_VERSION,
    "geometry": [
        "point",
        "samples_without_connections",
        "vector",
        "segment",
        "dashed",
        "polygon",
        "circle",
        "polynomial_function",
        "polynomial_secant",
        "polynomial_tangent",
        "matrix",
        "numberline",
    ],
    "polynomial": {
        "degree_max": 4,
        "coefficient_abs_max": 100,
        "coordinate_abs_max": 20,
        "evaluation": "trusted_code",
    },
    "matrix_max_rows_columns": 4,
    "themes": ["standard", "blue_gold", "high_contrast"],
    "font_scale": ["standard", "large"],
    "motion": ["standard", "minimal"],
    "unsupported": [
        "arbitrary_code",
        "3d",
        "non_polynomial_function_graphs",
        "external_assets",
        "open_points",
    ],
}
COLOR_ROLES = ("primary", "accent", "secondary", "warning", "text", "muted")
THEMES = {
    "standard": {
        "primary": "#69A9FF",
        "accent": "#F4CC77",
        "secondary": "#62DCC4",
        "warning": "#FC8E99",
        "text": "#E9F0FF",
        "muted": "#98ACC7",
        "background": "#0C1728",
        "surface": "#18283D",
        "axis": "#61758D",
    },
    "blue_gold": {
        "primary": "#69A9FF",
        "accent": "#FFD166",
        "secondary": "#ABCFFF",
        "warning": "#FF9D8A",
        "text": "#F5F7FF",
        "muted": "#B2BFDA",
        "background": "#10192D",
        "surface": "#1B2B47",
        "axis": "#8394B3",
    },
    "high_contrast": {
        "primary": "#70CFFF",
        "accent": "#FFFF80",
        "secondary": "#8CFFBC",
        "warning": "#FF9CB2",
        "text": "#FFFFFF",
        "muted": "#CCCCCC",
        "background": "#000000",
        "surface": "#171717",
        "axis": "#AAAAAA",
    },
}


def visual_parameters(spec) -> dict:
    return {
        "capability_version": CAPABILITY_VERSION,
        "spec": spec.model_dump(),
        "palette": dict(THEMES[spec.theme]),
        "font_multiplier": 1.12 if spec.font_scale == "large" else 1.0,
        "motion": spec.motion,
    }


def polynomial_value(coefficients, x):
    """Coefficients are in increasing power order; no source evaluation."""
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * x + coefficient
    return result


def polynomial_derivative(coefficients):
    return [i * coefficient for i, coefficient in enumerate(coefficients) if i]


def polynomial_roots_in_interval(coefficients, low, high):
    """Isolate real roots via derivative intervals for bounded degree <= 4."""
    coefficients = list(coefficients)
    while coefficients and coefficients[-1] == 0:
        coefficients.pop()
    if len(coefficients) < 2:
        return []
    critical = polynomial_roots_in_interval(polynomial_derivative(coefficients), low, high)
    boundaries = sorted({low, *critical, high})
    roots = [x for x in boundaries if abs(polynomial_value(coefficients, x)) < 1e-9]
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        sign = polynomial_value(coefficients, left)
        if sign * polynomial_value(coefficients, right) >= 0:
            continue
        for _ in range(64):
            middle = (left + right) / 2
            value = polynomial_value(coefficients, middle)
            if sign * value > 0:
                left = middle
            else:
                right = middle
        roots.append((left + right) / 2)
    return roots


def polynomial_bounds(coefficients, domain):
    low, high = domain
    points = [
        low,
        high,
        *polynomial_roots_in_interval(polynomial_derivative(coefficients), low, high),
    ]
    values = [polynomial_value(coefficients, x) for x in points]
    return min(values), max(values)


def function_line(obj):
    """Return a mathematically determined secant or tangent's slope and intercept."""
    x = obj.at
    y = polynomial_value(obj.coefficients, x)
    if obj.kind == "secant":
        slope = (polynomial_value(obj.coefficients, obj.through) - y) / (obj.through - x)
    else:
        slope = polynomial_value(polynomial_derivative(obj.coefficients), x)
    return slope, y - slope * x


def geometry_points(obj):
    if obj.kind == "function":
        low, high = obj.domain
        # Sampling density is fixed by code, never supplied by the model.
        return [
            (x := low + (high - low) * i / 256, polynomial_value(obj.coefficients, x))
            for i in range(257)
        ]
    if obj.kind in {"secant", "tangent"}:
        slope, intercept = function_line(obj)
        return [(x, slope * x + intercept) for x in obj.domain]
    return list(obj.points)


def geometry_evidence(obj):
    result = {"object_id": obj.object_id, "kind": obj.kind, "color_role": obj.color}
    if obj.kind in {"function", "secant", "tangent"}:
        result.update(coefficients=obj.coefficients, domain=obj.domain)
        if obj.kind != "function":
            slope, intercept = function_line(obj)
            result.update(slope=slope, intercept=intercept, at=obj.at)
        if obj.kind == "secant":
            result["through"] = obj.through
    else:
        result["points"] = obj.points
    return result
