from __future__ import annotations

import os

import pytest

from manimagent.cli import SMOKE_CODE
from manimagent.rendering import DockerRenderer
from manimagent.schemas import GenerationRequest, LessonPlan
from tests.helpers import make_engine


@pytest.mark.docker
@pytest.mark.skipif(
    os.getenv("MANIMAGENT_TEST_DOCKER") != "1", reason="opt-in real Docker rendering"
)
def test_real_renderer_full_pipeline_with_scripted_model(tmp_path):
    engine = make_engine(
        tmp_path, scripts={"coder": [{"code": SMOKE_CODE.replace("RendererSmoke", "Scene0")}]}
    )
    engine.renderer = DockerRenderer(engine.settings)
    assert not engine.renderer.preflight()
    job = engine.store.create(GenerationRequest(text="渲染环境与流程验收：二维向量投影"))
    result = engine.execute(job.job_id)
    assert result.status == "completed", result.message
    video = engine.store.resolve(result, result.final_artifact_id)
    assert video.stat().st_size > 10000
    assert len(result.scene_results[0].candidates[0].render.frames) == 4
    # The models are scripted; this verifies real rendering/assembly, not model quality.
    plan = LessonPlan.model_validate_json(
        (engine.store.job_dir(job.job_id) / "lesson_plan.json").read_text(encoding="utf-8")
    )
    assert result.scene_results[0].formal_review.qualifies(plan.scenes[0])
