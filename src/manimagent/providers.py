from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    def __init__(
        self, message: str, code: str = "invalid_provider_response", retryable: bool = False
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class InvalidResponse(ProviderError):
    pass


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

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        trace: Callable[[dict], None] | None = None,
        credential_resolver: Callable[[str], str | None] = os.getenv,
    ):
        self.settings = settings
        self.transport = transport
        self.trace = trace
        self.credential_resolver = credential_resolver

    async def _post(self, url: str, body: dict, headers: dict, timeout: float) -> httpx.Response:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
                return await client.post(url, json=body, headers=headers)

    def complete_text(
        self,
        role: str,
        instruction: str,
        payload: dict,
        budget: Budget,
        deadline: float | None = None,
    ) -> str:
        return self.complete(role, instruction, payload, None, budget, deadline=deadline)

    def complete(
        self,
        role: str,
        instruction: str,
        payload: dict,
        schema: type[T] | None,
        budget: Budget,
        deadline: float | None = None,
    ) -> T | str:
        cfg = self.settings.model_for(role)
        key = self.credential_resolver(cfg.api_key_env)
        if (
            not (cfg.base_url or cfg.generate_url)
            or not cfg.model
            or (cfg.auth_mode != "none" and not key)
        ):
            raise ProviderError(f"{role} 模型接口未配置", "model_unavailable")
        headers = {}
        if cfg.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        elif cfg.auth_mode == "api_key_header":
            headers[cfg.api_key_header] = key
        content = json.dumps(payload, ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": instruction
                + (
                    "\n只返回符合以下 schema 的 JSON 对象：\n"
                    + json.dumps(schema.model_json_schema(), ensure_ascii=False)
                    if schema
                    else ""
                ),
            },
            {"role": "user", "content": content},
        ]
        last_error: ProviderError = InvalidResponse("模型结果无效")
        for attempt in range(2):
            timeout = budget.before_call(deadline)
            body = {
                "model": cfg.model,
                "messages": messages,
                cfg.token_parameter: cfg.max_output_tokens,
            }
            if cfg.json_mode and schema:
                body["response_format"] = {"type": "json_object"}
            if self.trace:
                self.trace(
                    {"role": role, "attempt": attempt + 1, "event": "request", "request": body}
                )
            try:
                response = asyncio.run(
                    self._post(
                        cfg.generate_url or cfg.base_url + "/chat/completions",
                        body,
                        headers,
                        timeout,
                    )
                )
                if self.trace:
                    self.trace(
                        {
                            "role": role,
                            "attempt": attempt + 1,
                            "event": "http_response",
                            "http_status": response.status_code,
                        }
                    )
                if response.status_code >= 400:
                    code = {
                        401: "provider_auth_error",
                        403: "provider_auth_error",
                        402: "provider_quota_exceeded",
                        429: "provider_rate_limited",
                        408: "provider_timeout",
                        404: "model_unavailable",
                    }.get(
                        response.status_code,
                        "provider_unavailable"
                        if response.status_code >= 500
                        else "invalid_provider_response",
                    )
                    error = ProviderError(
                        f"{role} 接口返回 HTTP {response.status_code}",
                        code,
                        response.status_code in {408, 429} or response.status_code >= 500,
                    )
                    if response.status_code not in {408, 429} and response.status_code < 500:
                        raise error
                    last_error = error
                    continue
                data = response.json()
                if not isinstance(data, dict):
                    raise InvalidResponse("模型响应不是对象")
                usage = data.get("usage") or {}
                if not isinstance(usage, dict):
                    raise InvalidResponse("模型用量字段格式无效")
                for remote, local in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                ):
                    count = usage.get(remote)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        setattr(budget.usage, local, getattr(budget.usage, local) + count)
                        budget.usage.tokens_reported = True
                if (
                    budget.usage.input_tokens + budget.usage.output_tokens
                    > self.settings.max_total_tokens
                ):
                    raise BudgetExceeded("已达到模型使用量上限")
                choice = data["choices"][0]
                message = choice["message"]
                if not isinstance(choice, dict) or not isinstance(message, dict):
                    raise InvalidResponse("模型消息格式无效")
                raw = message.get("content")
                finish = choice.get("finish_reason")
                if self.trace:
                    self.trace(
                        {
                            "role": role,
                            "attempt": attempt + 1,
                            "raw_text": raw,
                            "finish_reason": finish,
                            "usage": usage or None,
                        }
                    )
                if finish == "length":
                    raise ProviderError("模型输出被截断，请增加输出预算后重试", "output_truncated")
                if message.get("refusal") or finish == "content_filter":
                    raise ProviderError("模型拒绝了本次回答", "provider_refusal")
                if message.get("tool_calls") or finish in {"tool_calls", "function_call"}:
                    raise ProviderError("本次调用不支持工具回合", "capability_unsupported")
                if finish != "stop":
                    raise InvalidResponse("模型未确认完整结束，不能发布答案")
                if not isinstance(raw, str) or not raw.strip():
                    raise InvalidResponse("模型没有返回完整文本")
                raw.encode("utf-8")
                result = schema.model_validate(parse_object(raw)) if schema else raw
                budget.remaining(deadline)
                return result
            except (ValidationError, KeyError, IndexError, ValueError, TypeError) as exc:
                last_error = InvalidResponse(f"{role} 返回结构不符合契约")
                last_error.__cause__ = exc
            except InvalidResponse as exc:
                last_error = exc
            except (httpx.HTTPError, TimeoutError):
                last_error = ProviderError(f"{role} 接口连接失败或超时", "provider_timeout", True)
                if self.trace:
                    self.trace(
                        {
                            "role": role,
                            "attempt": attempt + 1,
                            "event": "error",
                            "code": last_error.code,
                        }
                    )
            if attempt == 0 and schema:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "上次输出无法校验。请严格返回完整 schema JSON，不加解释或代码围栏。",
                    },
                ]
        raise last_error
