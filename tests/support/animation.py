from __future__ import annotations

import json

import httpx

from manimagent.config import ModelConfig, Settings
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
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

    def validate_layout(self, scene, directory, outline, scene_count, budget, index):
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
