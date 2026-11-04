"""Local UI configuration and separately stored credentials for the text workflow."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from manimagent.budget import Budget
from manimagent.config import ModelConfig, Settings
from manimagent.content_store import read_json_bytes, write_json
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from manimagent.llm.credentials import CredentialStore
from manimagent.schemas import Usage

ModelID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]


class ModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: ModelConfig
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_secret(self):
        if self.api_key is not None:
            secret = self.api_key.get_secret_value()
            if (
                not secret.strip()
                or len(secret) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in secret)
            ):
                raise ValueError("API Key 必须是非空的可打印 ASCII 字符串")
            if self.clear_api_key:
                raise ValueError("不能同时更新和停用密钥")
        return self


class RoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_writer: ModelID
    answer_checker: ModelID
    video_planner: ModelID | None = None
    scene_generator: ModelID | None = None
    plan_checker: ModelID | None = None


class LLMSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    models: dict[ModelID, ModelDraft] = Field(min_length=1, max_length=16)
    roles: RoleSelection
    answer_check: bool = True
    answer_context_tokens: int = Field(default=24000, ge=1024, le=2000000)

    @model_validator(mode="after")
    def references_exist(self):
        if any(
            identifier not in self.models
            for identifier in self.roles.model_dump(exclude_none=True).values()
        ):
            raise ValueError("回答角色引用了不存在的模型")
        return self


class LLMProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: ModelID
    draft: ModelDraft
    scope: Literal["text", "structured"] = "text"


class ProbeResult(BaseModel):
    ok: bool


class LLMSettingsStore:
    def __init__(self, settings: Settings, root: Path):
        self.root = root / "configuration"
        self.path = self.root / "llm.json"
        self.lock = threading.RLock()
        self.credentials = CredentialStore(self.root / "credentials.json", self.lock)
        self.probe_slots = threading.BoundedSemaphore(1)
        self.initial_settings = settings

    def read(self) -> dict:
        if not self.path.exists():
            return {"revision": 0, "models": {}, "roles": {}}
        return json.loads(read_json_bytes(self.path))

    def effective(self, settings: Settings | None = None) -> Settings:
        settings = (settings or self.initial_settings).model_copy(deep=True)
        with self.lock:
            saved = self.read()
            settings.models.update(
                {
                    identifier: ModelConfig.model_validate(cfg)
                    for identifier, cfg in saved["models"].items()
                }
            )
            settings.roles.update(saved["roles"])
            for name in ("answer_check", "answer_context_tokens"):
                if name in saved:
                    setattr(settings, name, saved[name])
        return settings

    def view(self, settings: Settings) -> dict:
        with self.lock:
            saved = self.read()
            models = {}
            for identifier, cfg in settings.models.items():
                # References are safe; the secret value is never serialized here.
                try:
                    configured = bool(self.credentials.resolve(cfg.api_key_env))
                except ContentError:
                    configured = False
                models[identifier] = {
                    "config": cfg.model_dump(),
                    "key_configured": configured,
                    "key_source": "local"
                    if cfg.api_key_env.startswith("local_secret_")
                    else "environment",
                }
            if not models:
                models["text"] = {
                    "config": ModelConfig().model_dump(),
                    "key_configured": False,
                    "key_source": "environment",
                }
            return {
                "revision": saved["revision"],
                "models": models,
                "roles": {
                    role: settings.model_id_for(role)
                    for role in (
                        "answer_writer",
                        "answer_checker",
                        "video_planner",
                        "scene_generator",
                        "plan_checker",
                    )
                },
                "answer_check": settings.answer_check,
                "answer_context_tokens": settings.answer_context_tokens,
                "protocol": "openai_chat",
            }

    def _draft_config(self, identifier: str, draft: ModelDraft, settings: Settings) -> ModelConfig:
        cfg = draft.config.model_copy(deep=True)
        # The caller cannot supply another model's credential reference through the UI API.
        previous = settings.models.get(identifier)
        cfg.api_key_env = previous.api_key_env if previous else ""
        if draft.clear_api_key:
            cfg.api_key_env = ""
        return cfg

    def update(self, request: LLMSettingsUpdate, settings: Settings) -> Settings:
        with self.lock:
            current = self.read()
            if request.expected_revision != current["revision"]:
                raise ContentError("revision_conflict", "配置已被修改，请重新载入后保存", 409)
            new_keys = {}
            updated = settings.model_copy(deep=True)
            for identifier, draft in request.models.items():
                cfg = self._draft_config(identifier, draft, settings)
                if draft.api_key is not None:
                    new_keys[identifier] = draft.api_key.get_secret_value()
                updated.models[identifier] = cfg
            updated.roles.update(request.roles.model_dump(exclude_none=True))
            updated.answer_check = request.answer_check
            updated.answer_context_tokens = request.answer_context_tokens
            # Write new credentials first, then commit their references. Old references remain
            # valid for already-accepted job snapshots, even when the UI changes its defaults.
            for identifier, reference in self.credentials.add(new_keys).items():
                updated.models[identifier].api_key_env = reference
            write_json(
                self.path,
                {
                    "revision": current["revision"] + 1,
                    "models": {
                        identifier: cfg.model_dump() for identifier, cfg in updated.models.items()
                    },
                    "roles": dict(updated.roles),
                    "answer_check": updated.answer_check,
                    "answer_context_tokens": updated.answer_context_tokens,
                },
            )
            return updated

    def probe(
        self,
        request: LLMProbeRequest,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict:
        cfg = self._draft_config(request.model_id, request.draft, settings)
        if not cfg.model.strip() or not (cfg.base_url or cfg.generate_url):
            raise ContentError("invalid_request", "请填写接口地址和模型名称")
        cfg.max_output_tokens = 256
        secret = (
            request.draft.api_key.get_secret_value()
            if request.draft.api_key
            else self.credentials.resolve(cfg.api_key_env)
        )
        if cfg.auth_mode != "none" and not secret:
            raise ContentError("credential_unavailable", "请填写 API Key 或选择无鉴权")
        if not self.probe_slots.acquire(blocking=False):
            raise ContentError("probe_busy", "已有连接测试正在运行，请稍后再试", 429)
        try:
            snapshot = settings.model_copy(deep=True)
            snapshot.models = {"text": cfg}
            snapshot.roles = {}
            snapshot.task_timeout, snapshot.model_timeout, snapshot.max_model_calls = 30, 15, 2
            usage = Usage()
            started = time.monotonic()
            provider = ChatProvider(
                snapshot, transport=transport, credential_resolver=lambda _: secret
            )
            budget = Budget(snapshot, usage)
            if request.scope == "text":
                provider.complete_text(
                    "answer_writer", "这是连接测试，请只回答 OK。", {"test": "text"}, budget
                )
            else:
                result = provider.complete(
                    "answer_checker",
                    "这是结构化输出测试，请返回 ok=true。",
                    {"test": "structured"},
                    ProbeResult,
                    budget,
                )
                if not result.ok:
                    raise ContentError("invalid_provider_response", "接口未通过结构化输出测试", 502)
            return {
                "ok": True,
                "scope": request.scope,
                "message": "文本调用成功" if request.scope == "text" else "结构化输出校验成功",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": usage.model_dump(),
            }
        finally:
            self.probe_slots.release()
