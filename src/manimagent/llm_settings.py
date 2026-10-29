"""Local UI configuration and separately stored credentials for the text workflow."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from manimagent.budget import Budget
from manimagent.config import ModelConfig, Settings
from manimagent.content_store import ContentError
from manimagent.providers import ChatProvider
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


class LLMSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    models: dict[ModelID, ModelDraft] = Field(min_length=1, max_length=16)
    roles: RoleSelection
    answer_check: bool = True
    answer_context_tokens: int = Field(default=24000, ge=1024, le=2000000)

    @model_validator(mode="after")
    def references_exist(self):
        if any(identifier not in self.models for identifier in self.roles.model_dump().values()):
            raise ValueError("回答角色引用了不存在的模型")
        return self


class LLMProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: ModelID
    draft: ModelDraft
    scope: Literal["text", "structured"] = "text"


class ProbeResult(BaseModel):
    ok: bool


def atomic_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    """Use the Windows user's credential protection; no external crypto dependency."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ContentError("credential_unavailable", "无法读取本地密钥，请重新输入", 503)
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class LLMSettingsStore:
    def __init__(self, settings: Settings, root: Path):
        self.root = root / "configuration"
        self.path = self.root / "llm.json"
        self.credentials_path = self.root / "credentials.json"
        self.lock = threading.RLock()
        self.probe_slots = threading.BoundedSemaphore(1)
        self.initial_settings = settings

    def read(self) -> dict:
        if not self.path.exists():
            return {"revision": 0, "models": {}, "roles": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _credentials(self) -> dict:
        if not self.credentials_path.exists():
            return {}
        return json.loads(self.credentials_path.read_text(encoding="utf-8"))

    def resolve(self, reference: str) -> str | None:
        if not reference.startswith("local_secret_"):
            return os.getenv(reference)
        with self.lock:
            entry = self._credentials().get(reference)
        if entry is None:
            return None
        if entry["encoding"] == "windows_dpapi":
            if os.name != "nt":
                raise ContentError(
                    "credential_unavailable", "此密钥绑定原 Windows 用户，请重新输入", 503
                )
            return dpapi(base64.b64decode(entry["value"]), decrypt=True).decode("utf-8")
        return entry["value"]

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
                    configured = bool(self.resolve(cfg.api_key_env))
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
            default = "text" if "text" in models else next(iter(models))
            return {
                "revision": saved["revision"],
                "models": models,
                "roles": {
                    role: settings.roles.get(role, default)
                    for role in ("answer_writer", "answer_checker")
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
            secrets = self._credentials()
            updated = settings.model_copy(deep=True)
            for identifier, draft in request.models.items():
                cfg = self._draft_config(identifier, draft, settings)
                if draft.api_key is not None:
                    reference = "local_secret_" + uuid.uuid4().hex
                    raw = draft.api_key.get_secret_value()
                    secrets[reference] = {
                        "encoding": "windows_dpapi" if os.name == "nt" else "plain",
                        "value": base64.b64encode(dpapi(raw.encode())).decode()
                        if os.name == "nt"
                        else raw,
                    }
                    cfg.api_key_env = reference
                updated.models[identifier] = cfg
            updated.roles.update(request.roles.model_dump())
            updated.answer_check = request.answer_check
            updated.answer_context_tokens = request.answer_context_tokens
            # Write new credentials first, then commit their references. Old references remain
            # valid for already-accepted job snapshots, even when the UI changes its defaults.
            atomic_private_json(self.credentials_path, secrets)
            atomic_private_json(
                self.path,
                {
                    "revision": current["revision"] + 1,
                    "models": {
                        identifier: cfg.model_dump() for identifier, cfg in updated.models.items()
                    },
                    "roles": {
                        role: updated.roles[role] for role in ("answer_writer", "answer_checker")
                    },
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
            else self.resolve(cfg.api_key_env)
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
