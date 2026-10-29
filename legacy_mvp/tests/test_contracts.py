from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from manimagent.artifacts import ArtifactStore
from manimagent.schemas import GenerationRequest, LessonPlan, ReviewReport, Scores
from tests.helpers import plan_data, review_data


@pytest.mark.parametrize("value", [None, 1, True, [], {}, "", "  \n\t", "x" * 3001, "\0", "\ud800"])
def test_invalid_text(value):
    with pytest.raises(ValidationError):
        GenerationRequest(text=value)


def test_unicode_length_and_whitespace():
    assert GenerationRequest(text="  a\n向量∑ 😀  ").text == "a\n向量∑ 😀"
    assert len(GenerationRequest(text="😀" * 3000).text) == 3000
    with pytest.raises(ValidationError):
        GenerationRequest(text="😀" * 3001)
    with pytest.raises(ValidationError):
        GenerationRequest(text="数学", file="some.pdf")


@pytest.mark.parametrize(
    "change", ["unknown_point", "duplicate_scene", "duplicate_step", "unsafe_id"]
)
def test_plan_references(change):
    data = plan_data(2)
    if change == "unknown_point":
        data["scenes"][0]["covered_point_ids"] = ["missing"]
    elif change == "duplicate_scene":
        data["scenes"][1]["scene_id"] = data["scenes"][0]["scene_id"]
    elif change == "duplicate_step":
        data["scenes"][1]["steps"][0]["step_id"] = data["scenes"][0]["steps"][0]["step_id"]
    else:
        data["scenes"][0]["scene_id"] = "../escape"
    with pytest.raises(ValidationError):
        LessonPlan.model_validate(data)


def test_high_score_does_not_override_critical_issue_or_missing_coverage():
    scene = LessonPlan.model_validate(plan_data()).scenes[0]
    report = ReviewReport.model_validate(
        review_data(
            issues=[
                {
                    "severity": "critical",
                    "description": "公式错误",
                    "evidence": "第二帧与题设不一致",
                }
            ]
        )
    )
    assert not report.qualifies(scene)
    report = ReviewReport.model_validate(review_data(required_point_coverage={}))
    assert not report.qualifies(scene)
    assert ReviewReport.model_validate(review_data()).qualifies(scene)


def test_invalid_scores_cannot_form_valid_review():
    with pytest.raises(ValidationError):
        ReviewReport(decision="pass")
    with pytest.raises(ValidationError):
        Scores(logic_flow=float("nan"), layout_occlusion=100, accuracy=100)


@pytest.mark.parametrize("name", ["CON", "nul", "COM1", "LPT9", "None"])
def test_plan_names_are_portable(name):
    data = plan_data()
    data["scenes"][0]["scene_id"] = name
    with pytest.raises(ValidationError):
        LessonPlan.model_validate(data)


def test_case_collisions_and_string_booleans_are_rejected():
    data = plan_data(2)
    data["scenes"][1]["scene_id"] = "SCENE0"
    with pytest.raises(ValidationError):
        LessonPlan.model_validate(data)
    with pytest.raises(ValidationError):
        ReviewReport.model_validate(review_data(required_point_coverage={"p0": "true"}))


def test_artifact_paths_and_public_metadata(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    manifest = store.create(GenerationRequest(text="向量投影"))
    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    with pytest.raises(ValueError):
        store.register(manifest, outside, "code", "bad")
    inside = store.job_dir(manifest.job_id) / "video.mp4"
    inside.write_bytes(b"video")
    aid = store.register(manifest, inside, "preview", "测试")
    assert store.resolve(manifest, aid) == inside
    assert str(tmp_path) not in json.dumps(store.public(manifest))
    manifest.artifacts[aid].path = "../../private.txt"
    with pytest.raises((FileNotFoundError, ValueError)):
        store.resolve(manifest, aid)
    with pytest.raises(FileNotFoundError):
        store.load("../outside")


def test_recovery_only_interrupts_unfinished_tasks(tmp_path):
    store = ArtifactStore(tmp_path)
    queued = store.create(GenerationRequest(text="向量"))
    running = store.create(GenerationRequest(text="矩阵"))
    running.status = "running"
    store.save(running)
    completed = store.create(GenerationRequest(text="投影"))
    completed.status = "completed"
    store.save(completed)
    assert store.recover() == 2
    assert store.load(queued.job_id).status == "interrupted"
    assert store.load(running.job_id).status == "interrupted"
    assert store.load(completed.job_id).status == "completed"
    assert not list(tmp_path.rglob("*.tmp"))
