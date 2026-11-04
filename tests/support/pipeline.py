from __future__ import annotations

import json

import httpx

from manimagent.config import ModelConfig, Settings
from manimagent.content_store import new_id
from manimagent.llm.client import ChatProvider


def settings_for(tmp_path, **kwargs):
    return Settings(
        runs_dir=tmp_path / "runs",
        models={
            "text": ModelConfig(
                base_url="https://model.example/team/v1", model="text-model", auth_mode="none"
            )
        },
        **kwargs,
    )


def controlled_provider(settings, calls, decision="pass", finish="stop", invalid_ref=False):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            content = json.dumps({"decision": decision, "message": "检查结果"})
        else:
            payload = json.loads(body["messages"][1]["content"])
            ref = payload["allowed_source_refs"][0]
            if invalid_ref:
                ref["block_id"] = new_id("block")
            content = (
                "# 投影解答\n\n投影系数是 $3$，投影向量为 $(3,0)$。\n\n"
                "\\[\\operatorname{proj}_{b}(a)=(3,0)\\]\n\n"
                f"[[{ref['material_id']}:{ref['block_id']}]]\n"
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    return ChatProvider(settings, httpx.MockTransport(handler))


def submit(client, inputs=None, **source_kwargs):
    return client.post(
        "/api/v2/generations",
        json={
            "source": {
                "type": "inputs",
                "inputs": inputs or [{"type": "text", "text": "a=(3,2), b=(1,0)"}],
                "instruction": "求投影并解释",
                **source_kwargs,
            }
        },
    )


def get_generation_answer(client, job):
    generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
    answer = client.get(f"/api/v2/answers/{generation['answer_id']}").json()
    return generation, answer
