from __future__ import annotations

import base64
from collections import defaultdict, deque
from pathlib import Path

from manimagent.artifacts import ArtifactStore
from manimagent.config import Settings
from manimagent.schemas import (
    CodeResponse,
    PlanCheck,
    PlanResponse,
    RenderResult,
    ReviewReport,
)
from manimagent.workflow import GenerationEngine

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
)


def plan_data(scenes: int = 1) -> dict:
    return {
        "title": "向量讲解",
        "goal": "解释向量",
        "given_facts": ["a=(3,2)"],
        "example_notes": [],
        "required_points": [
            {"point_id": f"p{i}", "description": f"知识点{i}"} for i in range(scenes)
        ],
        "scenes": [
            {
                "scene_id": f"scene{i}",
                "class_name": f"Scene{i}",
                "goal": "解释向量",
                "covered_point_ids": [f"p{i}"],
                "steps": [
                    {"step_id": f"step{i}", "explanation": "展示向量", "visual_action": "画箭头"}
                ],
                "formulas": [],
                "duration_seconds": 10,
            }
            for i in range(scenes)
        ],
    }


def review_data(decision: str = "pass", points: list[str] | None = None, **kwargs) -> dict:
    data = {
        "decision": decision,
        "scores": {"logic_flow": 90, "layout_occlusion": 90, "accuracy": 90},
        "issues": [],
        "required_point_coverage": {p: True for p in (points or ["p0"])},
    }
    data.update(kwargs)
    return data


class ScriptedProvider:
    """Test double only: production has no mock model or automatic success mode."""

    def __init__(self, scripts: dict | None = None, scenes: int = 1):
        self.scripts = defaultdict(deque, {k: deque(v) for k, v in (scripts or {}).items()})
        self.scenes = scenes
        self.calls = []

    def complete(self, role, instruction, payload, schema, budget, images=None, deadline=None):
        budget.before_call(deadline)
        self.calls.append((role, payload, images))
        if self.scripts[role]:
            value = self.scripts[role].popleft()
            if isinstance(value, Exception):
                raise value
            return schema.model_validate(value)
        if schema is PlanResponse:
            return PlanResponse(status="ready", plan=plan_data(self.scenes))
        if schema is PlanCheck:
            return PlanCheck(decision="pass")
        if schema is CodeResponse:
            name = payload["scene"]["class_name"]
            return CodeResponse(
                code=f"from manim import *\nclass {name}(Scene):\n    def construct(self):\n        self.wait(1)\n"
            )
        if schema is ReviewReport:
            return ReviewReport.model_validate(
                review_data(points=payload["scene"]["covered_point_ids"])
            )
        raise AssertionError(f"Unexpected model schema {schema}")


class FakeRenderer:
    """Records the protocol without claiming to produce real MP4 files."""

    def __init__(self, outcomes: list[str] | None = None):
        self.outcomes = deque(outcomes or [])
        self.calls = []
        self.assemblies = 0

    def preflight(self):
        return []

    def render(self, code, class_name, directory, formal, timeout):
        self.calls.append((code, formal, timeout))
        directory.mkdir(parents=True, exist_ok=True)
        status = self.outcomes.popleft() if self.outcomes else "success"
        if status != "success":
            return RenderResult(status="error", error_category=status, message="测试错误")
        video = directory / "video.mp4"
        video.write_bytes(b"test-double-not-a-real-video")
        frames = []
        for i in range(4):
            frame = directory / f"frame{i}.png"
            frame.write_bytes(PNG)
            frames.append(str(frame))
        return RenderResult(
            status="success",
            video_path=str(video),
            frames=frames,
            timestamps=[1, 3, 5, 7],
            duration=8,
        )

    def assemble(self, videos: list[Path], directory, timeout):
        self.assemblies += 1
        assert videos and all(p.exists() for p in videos)
        directory.mkdir(parents=True, exist_ok=True)
        video = directory / "assembled.mp4"
        video.write_bytes(b"test-double-assembled")
        return RenderResult(status="success", video_path=str(video), duration=8 * len(videos))


def make_engine(tmp_path: Path, scripts=None, outcomes=None, scenes=1, **settings):
    cfg = Settings(runs_dir=tmp_path / "runs", **settings)
    return GenerationEngine(
        cfg, ArtifactStore(cfg.runs_dir), ScriptedProvider(scripts, scenes), FakeRenderer(outcomes)
    )
