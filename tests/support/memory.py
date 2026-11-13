"""Shared deterministic memory fixtures and offline API/provider helpers."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx

from manimagent.llm.client import ChatProvider
from manimagent.personalization import MemoryProposal
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
    value="blue_gold",
    field="visual.palette",
    scope="global",
    intent="observe",
):
    text = f"我偏好{value}。"
    accepted = store.accept(source, session, text, scope, read=False)
    return store.observe_proposals(
        [MemoryProposal(field=field, value=value, quote=text, scope=scope, intent=intent)],
        session_id=session,
        source_id=source,
        source_text=text,
        scope=scope,
        sequence=accepted["sequence"],
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
        proposals, overrides, exclusions, issues = [], [], [], []
        if "请详细讲解" in text:
            quote = "伪造的用户原话" if invalid_evidence else "请详细讲解"
            preference = {"field": "explanation.detail", "value": "detailed", "quote": quote}
            overrides.append(preference)
            if "这次" not in text:
                proposals.append(
                    {
                        **preference,
                        "intent": "default" if "以后" in text else "observe",
                        "scope": "global",
                    }
                )
        if "忘记蓝金" in text:
            proposals.append(
                {
                    "field": "visual.palette",
                    "value": "blue_gold",
                    "quote": "忘记蓝金",
                    "intent": "forget",
                    "scope": "global",
                }
            )
        if "以后蓝金" in text:
            proposals.append(
                {
                    "field": "visual.palette",
                    "value": "blue_gold",
                    "quote": "以后蓝金",
                    "intent": "default",
                    "scope": "global",
                }
            )
        if "三维" in text:
            issues.append({"stage": "video", "message": "不支持三维", "quote": "三维"})
        interpretation = {
            "intent": {"overrides": overrides, "exclude_memory": exclusions, "issues": issues},
            "proposals": proposals,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(interpretation)},
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


def pending(
    store,
    source="r1",
    session="s1",
    value="detailed",
    field="explanation.detail",
    intent="observe",
    scope="global",
    quote="请详细讲解",
    read=True,
):
    accepted = store.accept(source, session, quote, scope, read=read)
    return dict(
        proposals=[
            MemoryProposal(field=field, value=value, intent=intent, scope=scope, quote=quote)
        ],
        source_id=source,
        session_id=session,
        source_text=quote,
        scope=scope,
        sequence=accepted["sequence"],
    )


def observe_proposals(store, **kwargs):
    return store.observe_proposals(**pending(store, **kwargs))
