from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from manimagent.video.models import (
    digest,
)
from manimagent.video.renderer.process import NativeRenderer
from tests.support import wait_job
from tests.support.animation import TemplateRenderer, config, submit, transport


def test_both_outputs_share_answer_and_video_only_retry_never_calls_writer(tmp_path):
    settings = config(tmp_path)
    calls = []
    renderer = TemplateRenderer()
    with TestClient(create_app(settings, transport(settings, calls), renderer)) as client:
        job = submit(client)
        assert job["status"] == "completed", job
        generation = client.get(f"/api/v2/generations/{job['result']['id']}").json()
        assert all(s["status"] == "completed" for s in generation["stages"].values())
        aid, vid = generation["answer_id"], generation["video_id"]
        answer = client.get(f"/api/v2/answers/{aid}").json()
        video = client.get(f"/api/v2/videos/{vid}").json()
        assert video["answer_id"] == aid and video["answer_hash"] == digest(answer["markdown"])
        assert 120 <= video["duration_seconds"] <= 360
        assert video["audio_asset_id"] is None and video["visual_model_review"] == "not_run"
        assert all(u["intervals"] for u in video["unit_mapping"])
        assert len(video["frame_mapping"]) == 9
        assert client.get(f"/api/v2/videos/{vid}/content").content == b"controlled-complete-movie"
        assert client.get(f"/api/v2/videos/{vid}/frames/99").status_code == 404
        assert calls == [
            "writer",
            "answer_check",
            "outline",
            "chunk",
            "chunk",
            "chunk",
            "plan_check",
        ]
        before = calls.count("writer")
        response = client.post("/api/v2/videos", json={"answer_id": aid})
        assert response.json()["answer_id"] == aid
        retried = wait_job(client, response.json()["job_id"])
        assert retried["status"] == "completed", retried
        assert calls.count("writer") == before and calls.count("answer_check") == 1


@pytest.mark.parametrize("fault", ["render", "check"])
def test_video_failure_keeps_published_answer_and_marks_partial(tmp_path, fault):
    settings = config(tmp_path)
    calls = []
    with TestClient(
        create_app(
            settings,
            transport(settings, calls, "fail" if fault == "check" else "pass"),
            TemplateRenderer(fail=fault == "render"),
        )
    ) as client:
        job = submit(client)
        assert job["status"] == "partial", job
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        assert generation["answer_id"] and generation["video_id"] is None
        assert generation["stages"]["answer"]["status"] == "completed"
        assert client.get(f"/api/v2/answers/{generation['answer_id']}/markdown").status_code == 200
        assert not list((tmp_path / "private/videos").glob("*.json"))


def test_revision_requires_fresh_content_check_before_video(tmp_path):
    settings = config(tmp_path)
    calls = []
    with TestClient(create_app(settings, transport(settings, calls), TemplateRenderer())) as client:
        job = submit(client, "text")
        generation = client.get(f"/api/v2/generations/{job['result']['id']}").json()
        original = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
        revised = client.post(
            "/api/v2/answers/revisions",
            json={
                "base_answer_id": original["answer_id"],
                "markdown": original["markdown"] + "\n\n复核结果保持一致。",
            },
        ).json()
        response = client.post("/api/v2/videos", json={"answer_id": revised["answer_id"]})
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        assert calls.count("writer") == 1 and calls.count("answer_check") == 2


def test_changing_checker_profile_invalidates_receipt_before_video(tmp_path):
    settings = config(tmp_path)
    calls = []
    with TestClient(create_app(settings, transport(settings, calls), TemplateRenderer())) as client:
        job = submit(client, "text")
        generation = client.get(f"/api/v2/generations/{job['result']['id']}").json()
    settings.models["text"].thinking = "enabled"
    with TestClient(create_app(settings, transport(settings, calls), TemplateRenderer())) as client:
        for _ in range(2):
            response = client.post("/api/v2/videos", json={"answer_id": generation["answer_id"]})
            job = wait_job(client, response.json()["job_id"])
            assert job["status"] == "completed", job
        assert calls.count("answer_check") == 2
        assert calls.count("writer") == 1


def test_resume_reuses_bound_chunks_but_rechecks_plan(tmp_path):
    settings = config(tmp_path)
    calls = []
    renderer = TemplateRenderer(fail=True)
    with TestClient(create_app(settings, transport(settings, calls), renderer)) as client:
        first = submit(client)
        generation = client.get(f"/api/v2/generations/{first['resource_ref']['id']}").json()
        before = list(calls)
        renderer.fail = False
        response = client.post(
            "/api/v2/videos",
            json={"answer_id": generation["answer_id"], "resume_job_id": first["job_id"]},
        )
        second = wait_job(client, response.json()["job_id"])
        assert second["status"] == "completed", second
        assert calls == before + ["plan_check"]


def test_resume_cannot_cross_answer_revision(tmp_path):
    settings = config(tmp_path)
    calls = []
    with TestClient(create_app(settings, transport(settings, calls), TemplateRenderer())) as client:
        job = submit(client)
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
        revised = client.post(
            "/api/v2/answers/revisions",
            json={
                "base_answer_id": answer["answer_id"],
                "markdown": answer["markdown"] + "\n\n补充版本。",
            },
        ).json()
        before = list(calls)
        response = client.post(
            "/api/v2/videos",
            json={"answer_id": revised["answer_id"], "resume_job_id": job["job_id"]},
        )
        failed = wait_job(client, response.json()["job_id"])
        assert failed["status"] == "failed" and failed["error"]["code"] == "invalid_reference"
        assert calls == before and failed["usage"]["model_calls"] == 0


def test_short_actual_video_cannot_pass_planned_duration_gate(tmp_path):
    from types import SimpleNamespace

    from manimagent.budget import Budget
    from manimagent.schemas import Usage

    class ShortRenderer(NativeRenderer):
        def run(self, *args, **kwargs):
            return {"media": {"duration_seconds": 30}, "events": []}

    with pytest.raises(ContentError, match="时长"):
        ShortRenderer().render(
            SimpleNamespace(duration_seconds=180, model_dump=lambda: {}),
            tmp_path,
            SimpleNamespace(outline=SimpleNamespace(title="测试"), scenes=[1]),
            Budget(config(tmp_path), Usage()),
            0,
        )


def test_short_video_repair_expands_explanation_instead_of_shortening_it(tmp_path):
    settings = config(tmp_path)
    calls, repairs = [], []
    base = transport(settings, calls).transport

    def handler(request):
        response = base.handle_request(request)
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        if "仅生成指定场景" not in body["messages"][0]["content"]:
            return response
        if payload.get("feedback"):
            repairs.append(payload["feedback"])
            data = response.json()
            scene = json.loads(data["choices"][0]["message"]["content"])
            for beat in scene["beats"]:
                beat["screen_text"] += "并核对各项。"
            data["choices"][0]["message"]["content"] = json.dumps(scene, ensure_ascii=False)
            return httpx.Response(200, json=data)
        data = response.json()
        scene = json.loads(data["choices"][0]["message"]["content"])
        for i, beat in enumerate(scene["beats"]):
            beat.update(
                headline=f"步骤{i}",
                screen_text="保留给定条件，核对结果。",
                spoken_text="先核对给定的条件，再计算并验证结果。",
                cue_text="先核对",
            )
        data["choices"][0]["message"]["content"] = json.dumps(scene, ensure_ascii=False)
        return httpx.Response(200, json=data)

    with TestClient(
        create_app(
            settings,
            ChatProvider(settings, transport=httpx.MockTransport(handler)),
            TemplateRenderer(),
        )
    ) as client:
        job = submit(client)
        assert job["status"] == "completed", job
        assert 1 <= len(repairs) <= 2
        assert all("required_scene_min_seconds" in feedback for feedback in repairs)


@pytest.mark.parametrize(
    "layout_failures, revisions, expected",
    [
        (0, 2, "completed"),
        (0, 3, "partial"),
        (2, 0, "completed"),
        (3, 0, "partial"),
        (1, 1, "completed"),
        (1, 2, "partial"),
    ],
)
def test_repairs_share_two_slots_and_always_recheck_final_plan(
    tmp_path, layout_failures, revisions, expected
):
    settings = config(tmp_path)
    calls, checked, feedback = [], [], []
    base = transport(settings, calls).transport

    class LayoutFailures(TemplateRenderer):
        attempts = 0

        def validate_layout(self, *args):
            self.attempts += 1
            if self.attempts <= layout_failures:
                raise ContentError("render_layout", "测试排版未通过")
            return super().validate_layout(*args)

    def handler(request):
        response = base.handle_request(request)
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        if "仅生成指定场景" in body["messages"][0]["content"] and payload.get("feedback"):
            feedback.append(payload)
            assert payload["previous_scene"]
        if "独立对照固定正文" in body["messages"][0]["content"]:
            checked.append(payload)
            if len(checked) <= revisions:
                data = response.json()
                report = json.loads(data["choices"][0]["message"]["content"])
                report.update(
                    decision="revise", message="修正场景一的解释", problem_scene_ids=["s1"]
                )
                data["choices"][0]["message"]["content"] = json.dumps(report)
                return httpx.Response(200, json=data)
        return response

    renderer = LayoutFailures()
    with TestClient(
        create_app(
            settings, ChatProvider(settings, transport=httpx.MockTransport(handler)), renderer
        )
    ) as client:
        job = submit(client)
        assert job["status"] == expected, job
        assert len(feedback) <= 2
        if expected == "completed":
            assert len(checked) == revisions + 1
        else:
            assert not renderer.render_calls
