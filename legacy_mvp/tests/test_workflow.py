from __future__ import annotations

from manimagent.providers import InvalidResponse, ProviderError
from manimagent.schemas import GenerationRequest
from tests.helpers import make_engine, plan_data, review_data


def execute(engine, text="请解释向量投影"):
    return engine.execute(engine.store.create(GenerationRequest(text=text)).job_id)


def test_complete_two_scene_pipeline(tmp_path):
    engine = make_engine(tmp_path, scenes=2)
    result = execute(engine)
    assert result.status == "completed", result.message
    assert result.total_scenes == 2
    assert all(s.status == "completed" for s in result.scene_results)
    assert len(engine.renderer.calls) == 4
    assert [formal for _, formal, _ in engine.renderer.calls] == [False, True, False, True]
    assert engine.renderer.assemblies == 1
    assert engine.store.resolve(result, result.final_artifact_id).name == "final.mp4"
    assert sum(role == "vision_checker" for role, _, _ in engine.provider.calls) == 4


def test_missing_input_does_not_render(tmp_path):
    engine = make_engine(
        tmp_path, scripts={"planner": [{"status": "needs_input", "message": "请提供矩阵"}]}
    )
    result = execute(engine, "求这个矩阵的特征值")
    assert result.status == "needs_input" and "矩阵" in result.message
    assert not engine.renderer.calls


def test_url_is_not_fetched_or_sent_to_model(tmp_path):
    engine = make_engine(tmp_path)
    assert execute(engine, "https://example.com/paper").status == "needs_input"
    assert not engine.provider.calls


def test_execution_and_visual_budgets_are_cumulative(tmp_path):
    engine = make_engine(
        tmp_path,
        outcomes=["python", "success", "python", "success", "python"],
        scripts={"vision_checker": [review_data("revise"), review_data("revise")]},
    )
    result = execute(engine)
    scene = result.scene_results[0]
    assert scene.execution_repairs == 2 and scene.visual_revisions == 2
    assert len(scene.candidates) == 5
    assert len(engine.renderer.calls) == 5
    assert result.status == "needs_review"
    assert not engine.renderer.assemblies and not result.final_artifact_id


def test_visual_revision_produces_new_candidate(tmp_path):
    engine = make_engine(tmp_path, scripts={"vision_checker": [review_data("revise")]})
    result = execute(engine)
    assert result.status == "completed", result.message
    assert result.scene_results[0].visual_revisions == 1
    assert result.scene_results[0].selected_attempt_id == "candidate_01"


def test_earlier_approved_preview_survives_formal_crash(tmp_path):
    engine = make_engine(tmp_path, outcomes=["success", "python"])
    result = execute(engine)
    scene = result.scene_results[0]
    assert result.status == "needs_review" and scene.selected_attempt_id == "candidate_00"
    assert scene.candidates[0].video_artifact_id in result.artifacts
    assert not result.final_artifact_id
    assert not (engine.store.job_dir(result.job_id) / "final.mp4").exists()


def test_unavailable_visual_review_does_not_trigger_layout_changes(tmp_path):
    engine = make_engine(tmp_path, scripts={"vision_checker": [ProviderError("检查服务不可用")]})
    result = execute(engine)
    scene = result.scene_results[0]
    assert result.status == "needs_review"
    assert scene.candidates[0].review.review_status == "unavailable"
    assert scene.visual_revisions == 0


def test_invalid_formal_review_never_inherits_preview_pass(tmp_path):
    engine = make_engine(
        tmp_path, scripts={"vision_checker": [review_data(), InvalidResponse("无效JSON")]}
    )
    result = execute(engine)
    assert result.status == "needs_review"
    assert result.scene_results[0].formal_review.review_status == "invalid"
    assert not engine.renderer.assemblies


def test_high_score_with_major_issue_cannot_complete(tmp_path):
    bad = review_data(
        issues=[{"severity": "major", "description": "关键步骤遗漏", "evidence": "四帧均未展示"}]
    )
    engine = make_engine(tmp_path, scripts={"vision_checker": [bad]}, max_visual_revisions=0)
    result = execute(engine)
    assert result.status == "needs_review" and not result.final_artifact_id


def test_partial_scene_success_never_assembles_partial_final(tmp_path):
    engine = make_engine(
        tmp_path, scenes=2, outcomes=["success", "success", "python"], max_execution_repairs=0
    )
    result = execute(engine)
    assert result.status == "needs_review"
    assert result.scene_results[0].status == "completed"
    assert result.scene_results[1].status == "failed"
    assert not engine.renderer.assemblies


def test_revised_plan_binds_candidates_to_new_revision(tmp_path):
    engine = make_engine(
        tmp_path,
        scripts={
            "plan_checker": [
                {"decision": "revise", "message": "补充步骤"},
                {"decision": "pass"},
            ]
        },
    )
    result = execute(engine)
    assert result.status == "completed"
    assert result.plan_revision == 2
    assert all(c.plan_revision == 2 for c in result.scene_results[0].candidates)


def test_plan_repair_is_bounded(tmp_path):
    engine = make_engine(
        tmp_path,
        scripts={
            "plan_checker": [
                {"decision": "revise", "message": "仍有问题"},
                {"decision": "revise", "message": "仍有问题"},
            ]
        },
    )
    result = execute(engine)
    assert result.status == "failed"
    assert not engine.renderer.calls
    assert sum(role == "planner" for role, _, _ in engine.provider.calls) == 2


def test_environment_errors_are_not_hidden_by_code_rewrites(tmp_path):
    engine = make_engine(tmp_path, outcomes=["environment"])
    result = execute(engine)
    assert result.status == "failed"
    assert result.scene_results[0].execution_repairs == 0
    assert len(engine.renderer.calls) == 1


def test_model_budget_ends_task(tmp_path):
    engine = make_engine(tmp_path, max_model_calls=2)
    result = execute(engine)
    assert result.status == "failed"
    assert result.usage.model_calls == 2
    assert not engine.renderer.calls


def test_executed_job_is_not_repeated(tmp_path):
    engine = make_engine(tmp_path)
    result = execute(engine)
    count = len(engine.provider.calls)
    assert engine.execute(result.job_id).status == "completed"
    assert len(engine.provider.calls) == count


def test_fewer_scenes_configuration_is_enforced(tmp_path):
    engine = make_engine(
        tmp_path,
        max_scenes=1,
        scripts={
            "planner": [
                {"status": "ready", "plan": plan_data(2)},
                {"status": "ready", "plan": plan_data(2)},
            ]
        },
    )
    assert execute(engine).status == "failed"
    assert not engine.renderer.calls
