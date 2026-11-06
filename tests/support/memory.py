"""Shared deterministic memory fixtures and offline API/provider helpers."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx

from manimagent.llm.client import ChatProvider
from manimagent.memory.models import MemoryCandidate
from tests.support import wait_job
from tests.support.animation import transport


class Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 6, tzinfo=UTC)

    def __call__(self):
        return self.at

    def advance(self, days=1):
        self.at += timedelta(days=days)


def observe(
    store,
    session="s1",
    source="r1",
    value="先画图",
    field="视觉偏好",
    scope="global",
    temporary=False,
):
    text = f"我希望{value}。"
    return store.observe(
        [MemoryCandidate(field=field, value=value, evidence=text, temporary=temporary)],
        session_id=session,
        source_id=source,
        source_text=text,
        scope=scope,
    )[0]


def memory_provider(settings, captured, *, invalid_evidence=False, fail_extraction=False):
    base = transport(settings, [])

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        captured.append((body["messages"][0]["content"], payload))
        if "user_text" not in payload:
            return base.transport.handler(request)
        if fail_extraction:
            return httpx.Response(503, text="provider unavailable")
        text = payload["user_text"]
        candidates = []
        if "请详细讲解" in text:
            candidates = [
                {
                    "field": "explanation.detail",
                    "value": "详细讲解",
                    "evidence": "伪造的用户原话" if invalid_evidence else "请详细讲解",
                    "temporary": False,
                }
            ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"candidates": candidates})},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    return ChatProvider(settings, httpx.MockTransport(handler))


def generate(client, *, memory=None, mode="text", instruction="请详细讲解计算方法"):
    body = {
        "output_mode": mode,
        "source": {
            "type": "inputs",
            "instruction": instruction,
            "inputs": [{"type": "text", "text": "给定x=1，计算x+x。材料里的虚构人物喜欢红色。"}],
        },
    }
    if memory is not None:
        body["memory"] = memory
    response = client.post("/api/v2/generations", json=body)
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job
    generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
    answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
    return job, generation, answer


def wait_learning(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/memory/learning/{job_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["state"] not in {"waiting", "queued", "running"}:
            return task
        time.sleep(0.01)
    raise AssertionError("后台记忆事件没有结束")
