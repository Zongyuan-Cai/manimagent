from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from manimagent.animation import teaching_units
from manimagent.animation_models import (
    BeatDraft,
    Diagram,
    GraphicObject,
    SceneDraft,
    digest,
)
from manimagent.animation_renderer import NativeRenderer
from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.content_store import ContentError
from manimagent.providers import ChatProvider
from tests.support import wait_job


class TemplateRenderer:
    """Controlled renderer evidence, deliberately distinct from real rendering tests."""

    def __init__(self, fail=False):
        self.fail = fail
        self.render_calls = []

    def preflight(self):
        return []

    def validate_runtime(self, directory, budget):
        return {"status": "success", "test_renderer": True}

    def render(self, scene, directory, plan, budget, index):
        self.render_calls.append(scene.scene_id)
        if self.fail:
            raise ContentError("render_failed", "测试渲染失败")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scene.mp4").write_bytes(b"controlled-scene")
        events = []
        frames = []
        for i, b in enumerate(scene.beats):
            (directory / f"frame_{i}.png").write_bytes(b"controlled-image")
            events.append(
                {
                    "beat_id": b.beat_id,
                    "unit_ids": b.unit_ids,
                    "start_seconds": b.start_seconds,
                    "stable_seconds": b.stable_seconds,
                    "end_seconds": b.end_seconds,
                    "objects": [],
                }
            )
            frames.append({"path": f"frame_{i}.png", "actual_seconds": b.stable_seconds + 0.5})
        return {
            "video_path": "scene.mp4",
            "events": events,
            "frames": frames,
            "media": {"duration_seconds": scene.duration_seconds},
            "wall_seconds": 0.01,
        }

    def assemble(self, sources, directory, plan, budget):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "complete.mp4").write_bytes(b"controlled-complete-movie")
        return {"video_path": "complete.mp4", "media": {"duration_seconds": plan.duration_seconds}}


def config(tmp_path):
    return Settings(
        runs_dir=tmp_path,
        models={
            "text": ModelConfig(
                base_url="https://test.invalid/v1",
                model="controlled",
                auth_mode="none",
                context_window=128000,
            )
        },
        video_context_tokens=96000,
    )


def transport(settings, calls, check="pass"):
    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        payload = json.loads(body["messages"][1]["content"])
        if "先规划整体" in system:
            role = "outline"
            units = [u["unit_id"] for u in payload["teaching_units"]]
            output = {
                "status": "ready",
                "message": "教学范围适合",
                "title": "向量与计算",
                "audience": "首次学习本题方法的学生",
                "difficulty": "basic",
                "difficulty_reason": "单一方法分步推导",
                "duration_reason": "包括条件、推理、计算与验证",
                "scenes": [
                    {
                        "scene_id": f"s{i}",
                        "title": f"教学单元{i}",
                        "objective": "解释完整推理过程",
                        "unit_ids": units[i - 1 :: 3],
                        "beat_count": 3,
                    }
                    for i in range(1, 4)
                ],
            }
        elif "仅生成指定场景" in system:
            role = "chunk"
            scene = payload["scene"]
            output = {
                "scene_id": scene["scene_id"],
                "beats": [
                    {
                        "beat_id": f"b{i}",
                        "unit_ids": scene["unit_ids"],
                        "headline": f"单元{scene['scene_id']}第{i}步的推理",
                        "screen_text": f"这是场景{scene['scene_id']}的第{i}步。先保留题目给定的条件，解释计算依据，再逐项计算并核对结果，确认所得结论与条件相符。",
                        "formulas": [f"x={i}"],
                        "spoken_text": "先保留题目给定的条件，然后解释这一操作的依据，最后核对得到的结果。",
                        "cue_text": "先保留",
                        "cue_objects": [],
                        "reading_reason": "阅读条件与推理依据",
                        "diagram": {"kind": "none"},
                    }
                    for i in range(1, 4)
                ],
            }
        elif "独立对照固定正文" in system:
            role = "plan_check"
            plan = payload["plan"]
            output = {
                "decision": check,
                "message": "全部单元已检查" if check == "pass" else "测试中无法确认覆盖",
                "units": [
                    {
                        "unit_id": u["unit_id"],
                        "decision": "pass" if check == "pass" else "unknown",
                        "scene_ids": [
                            s["scene_id"] for s in plan["scenes"] if u["unit_id"] in s["unit_ids"]
                        ],
                        "evidence": "对应节拍包含条件、依据和结论",
                    }
                    for u in plan["teaching_units"]
                ],
                "problem_scene_ids": [],
            }
        elif "检查候选解答" in system:
            role = "answer_check"
            output = {"decision": "pass", "message": "完整解答已核对"}
        else:
            role = "writer"
            ref = payload["allowed_source_refs"][0]
            marker = f"[[{ref['material_id']}:{ref['block_id']}]]"
            output = f"# 已知条件\n\n给定 x=1。{marker}\n\n# 计算依据\n\n采用给定数值直接相加，1+1=2。\n\n# 结论核对\n\n答案为2，满足给定计算。"
        calls.append(role)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30},
            },
        )

    return ChatProvider(settings, transport=httpx.MockTransport(handler))


def submit(client, mode="both"):
    response = client.post(
        "/api/v2/generations",
        json={
            "output_mode": mode,
            "source": {
                "type": "inputs",
                "instruction": "完整讲解计算方法",
                "inputs": [{"type": "text", "text": "给定 x=1，计算 x+x 并解释。"}],
            },
        },
    )
    assert response.status_code == 202, response.text
    return wait_job(client, response.json()["job_id"])


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


def test_native_environment_never_contains_api_keys_or_proxy_credentials(monkeypatch):
    monkeypatch.setenv("MY_LLM_API_KEY", "private-value")
    monkeypatch.setenv("HTTPS_PROXY", "https://private:password@proxy.invalid")
    child = NativeRenderer.child_environment()
    assert "MY_LLM_API_KEY" not in child and "HTTPS_PROXY" not in child
    assert child["MPLBACKEND"] == "Agg"


def test_source_ranges_preserve_unicode_code_fences_and_display_math():
    markdown = "# 向量😀\n\n已知条件。\n\n```python\nx = 1\n\nprint(x)\n```\n\n\\[\na+b\n\n=c\n\\]\n\n结论。"
    units = teaching_units(markdown)
    assert all(markdown[u.start : u.end] == u.text and digest(u.text) == u.sha256 for u in units)
    assert any("x = 1\n\nprint(x)" in u.text for u in units)
    assert any("a+b\n\n=c" in u.text for u in units)
    assert all(
        c.isspace() or any(u.start <= i < u.end for u in units) for i, c in enumerate(markdown)
    )


@pytest.mark.parametrize("points", [[[0, 0], [0, 0]], [[float("nan"), 0], [1, 1]], [[0, 0]]])
def test_degenerate_or_nonfinite_vectors_are_rejected(points):
    with pytest.raises(ValidationError):
        GraphicObject(object_id="a", kind="vector", points=points)


def test_outside_coordinates_and_arbitrary_code_are_rejected():
    with pytest.raises(ValidationError):
        Diagram(kind="plane", objects=[{"object_id": "p", "kind": "point", "points": [[15, 1]]}])
    with pytest.raises(ValidationError):
        SceneDraft(scene_id="s1", beats=[], python_code="print('unsafe')")


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


@pytest.mark.parametrize(
    "formula", [r"\input{private}", r"\begin{matrix}1\end{matrix}", "$x$", "中文公式"]
)
def test_mathtext_rejects_file_macros_environments_and_non_math_input(formula):
    with pytest.raises(ValidationError):
        BeatDraft(
            beat_id="b1",
            unit_ids=["u001"],
            headline="读取公式",
            screen_text="解释这个公式的计算依据和意义。",
            formulas=[formula],
            spoken_text="先解释这个公式的每个符号，再对应到题目条件。",
            cue_text="先解释",
            reading_reason="理解公式",
        )


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


def test_mathtext_aliases_and_prompt_examples_keep_command_prefixes():
    from manimagent.animation import OUTLINE_PROMPT, PLAN_PROMPT, SCENE_PROMPT

    slash = chr(92)
    assert BeatDraft.safe_formulas(["frac{1}{2}"]) == [slash + "frac{1}{2}"]
    assert BeatDraft.safe_formulas([slash + "lVert b" + slash + "rVert"]) == [
        slash + "Vert b" + slash + "Vert"
    ]
    assert slash + "frac{a}{b}" in SCENE_PROMPT and slash + "mathbf{a}" in SCENE_PROMPT
    assert all(
        ord(c) >= 32 or c in "\n\r\t"
        for prompt in [OUTLINE_PROMPT, SCENE_PROMPT, PLAN_PROMPT]
        for c in prompt
    )
