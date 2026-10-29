from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


class InvalidResponse(ProviderError):
    pass


class Provider(Protocol):
    def complete(
        self,
        role: str,
        instruction: str,
        payload: dict,
        schema: type[T],
        budget: Budget,
        images: list[Path] | None = None,
        deadline: float | None = None,
    ) -> T: ...


def parse_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise InvalidResponse("模型未返回有效 JSON 对象") from exc
    if not isinstance(value, dict):
        raise InvalidResponse("模型结果必须是 JSON 对象")
    return value


class ChatProvider:
    """Configurable Chat Completions adapter; no SDK-specific global state."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def _post(self, url: str, body: dict, key: str, timeout: float) -> httpx.Response:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
                return await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})

    def complete(
        self,
        role: str,
        instruction: str,
        payload: dict,
        schema: type[T],
        budget: Budget,
        images: list[Path] | None = None,
        deadline: float | None = None,
    ) -> T:
        cfg = self.settings.model_for(role)
        key = os.getenv(cfg.api_key_env)
        if not cfg.base_url or not cfg.model or not key:
            raise ProviderError(f"{role} 模型接口未配置")
        content: list[dict] = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
        for path in images or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(path.read_bytes()).decode(),
                        "detail": "high",
                    },
                }
            )
        messages = [
            {
                "role": "system",
                "content": instruction
                + "\n只返回符合以下 schema 的 JSON 对象：\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False),
            },
            {"role": "user", "content": content if images else content[0]["text"]},
        ]
        last_error: ProviderError = InvalidResponse("模型结果无效")
        for attempt in range(2):
            timeout = budget.before_call(deadline)
            body = {
                "model": cfg.model,
                "messages": messages,
                cfg.token_parameter: cfg.max_output_tokens,
            }
            if cfg.json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                response = asyncio.run(
                    self._post(cfg.base_url + "/chat/completions", body, key, timeout)
                )
                if response.status_code >= 400:
                    error = ProviderError(f"{role} 接口返回 HTTP {response.status_code}")
                    if response.status_code not in {408, 429} and response.status_code < 500:
                        raise error
                    last_error = error
                    continue
                data = response.json()
                usage = data.get("usage") or {}
                for remote, local in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                ):
                    count = usage.get(remote)
                    if isinstance(count, int) and count >= 0:
                        setattr(budget.usage, local, getattr(budget.usage, local) + count)
                        budget.usage.tokens_reported = True
                if (
                    budget.usage.input_tokens + budget.usage.output_tokens
                    > self.settings.max_total_tokens
                ):
                    raise BudgetExceeded("已达到模型使用量上限")
                raw = data["choices"][0]["message"]["content"]
                if not isinstance(raw, str):
                    raise InvalidResponse("模型没有返回文本 JSON")
                result = schema.model_validate(parse_object(raw))
                budget.remaining(deadline)
                return result
            except (ValidationError, KeyError, IndexError, ValueError, TypeError) as exc:
                last_error = InvalidResponse(f"{role} 返回结构不符合契约")
                last_error.__cause__ = exc
            except InvalidResponse as exc:
                last_error = exc
            except (httpx.HTTPError, TimeoutError):
                last_error = ProviderError(f"{role} 接口连接失败或超时")
            if attempt == 0:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "上次输出无法校验。请严格返回完整 schema JSON，不加解释或代码围栏。",
                    },
                ]
        raise last_error
