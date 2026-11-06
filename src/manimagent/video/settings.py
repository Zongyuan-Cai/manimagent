"""Local speech configuration and bounded preview, separate from text model settings."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from manimagent.budget import Budget
from manimagent.config import Settings, TTSConfig
from manimagent.errors import ContentError
from manimagent.llm.credentials import CredentialDraft, CredentialStore, same_endpoint_origin
from manimagent.schemas import Usage
from manimagent.storage import read_json_bytes, write_json
from manimagent.video.tts import MiMoTTS


class TTSDraft(CredentialDraft):
    config: TTSConfig


class TTSSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    draft: TTSDraft


class TTSProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: TTSDraft
    text: str = Field(default="你好，让我们一步一步理解这道题。", min_length=1, max_length=200)

    @field_validator("text")
    @classmethod
    def readable_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("请输入可朗读的短文本")
        return value


class TTSSettingsStore:
    def __init__(self, root: Path, credentials: CredentialStore):
        self.path = root / "configuration" / "tts.json"
        self.credentials = credentials
        self.lock = threading.RLock()
        self.probe_slots = threading.BoundedSemaphore(1)

    def read(self) -> dict:
        if not self.path.exists():
            return {"revision": 0}
        return json.loads(read_json_bytes(self.path))

    def effective(self, settings: Settings) -> Settings:
        updated = settings.model_copy(deep=True)
        with self.lock:
            saved = self.read()
            if "config" in saved:
                updated.tts = TTSConfig.model_validate(saved["config"])
        return updated

    def status(self, settings: Settings) -> dict:
        try:
            configured = bool(self.credentials.resolve(settings.tts.api_key_env))
        except ContentError:
            configured = False
        return {
            "enabled": settings.tts.enabled,
            "key_configured": configured,
            "ready": configured,
            "state": "disabled"
            if not settings.tts.enabled
            else "ready"
            if configured
            else "needs_configuration",
            "issues": [] if configured else ["语音 API Key 未配置，请在语音设置中填写并试听"],
        }

    def view(self, settings: Settings) -> dict:
        with self.lock:
            return {
                "revision": self.read()["revision"],
                "config": settings.tts.model_dump(),
                "protocol": "mimo_chat",
                "key_source": "local"
                if settings.tts.api_key_env.startswith("local_secret_")
                else "environment"
                if settings.tts.api_key_env
                else "none",
                **self.status(settings),
            }

    def draft_config(self, draft: TTSDraft, settings: Settings) -> TTSConfig:
        cfg = draft.config.model_copy(deep=True)
        # A draft cannot choose the text model's secret or another environment variable.
        cfg.api_key_env = "" if draft.clear_api_key else settings.tts.api_key_env
        if (
            not draft.clear_api_key
            and draft.api_key is None
            and not same_endpoint_origin(settings.tts.base_url, cfg.base_url)
            and self.credentials.resolve(settings.tts.api_key_env)
        ):
            raise ContentError(
                "credential_origin_changed",
                "语音服务地址已变更，请重新输入该服务的 API Key，或先清除原语音密钥关联",
            )
        return cfg

    def update(self, request: TTSSettingsUpdate, settings: Settings) -> Settings:
        with self.lock:
            saved = self.read()
            if saved["revision"] != request.expected_revision:
                raise ContentError("revision_conflict", "语音配置已被修改，请重新载入后保存", 409)
            cfg = self.draft_config(request.draft, settings)
            if request.draft.api_key is not None:
                cfg.api_key_env = self.credentials.add(
                    {"tts": request.draft.api_key.get_secret_value()}
                )["tts"]
            write_json(self.path, {"revision": saved["revision"] + 1, "config": cfg.model_dump()})
            updated = settings.model_copy(deep=True)
            updated.tts = cfg
            return updated

    def probe(
        self,
        request: TTSProbeRequest,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict:
        cfg = self.draft_config(request.draft, settings)
        secret = (
            request.draft.api_key.get_secret_value()
            if request.draft.api_key
            else self.credentials.resolve(cfg.api_key_env)
        )
        if not secret:
            raise ContentError(
                "tts_not_configured", "请填写语音 API Key 后试听；核心模型密钥不会用于语音"
            )
        if not self.probe_slots.acquire(blocking=False):
            raise ContentError("probe_busy", "已有语音试听正在运行，请稍后再试", 429)
        try:
            # A preview never changes saved defaults, persists input, or retries a paid call.
            cfg.max_retries, cfg.max_calls = 0, 1
            cfg.timeout = min(cfg.timeout, 30)
            cfg.max_audio_seconds = min(cfg.max_audio_seconds, 30)
            cfg.max_response_bytes = min(cfg.max_response_bytes, 4 * 1024 * 1024)
            snapshot = settings.model_copy(deep=True)
            snapshot.task_timeout = 30
            budget = Budget(snapshot, Usage())
            speech = MiMoTTS(cfg, credential_resolver=lambda _: secret, transport=transport)
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="manimagent-preview-") as folder:
                target = Path(folder) / "preview.wav"
                metadata = speech.synthesize(request.text, target, budget)
                encoded = base64.b64encode(target.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "message": "语音合成成功，请播放试听；本次测试未保存配置",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "calls": speech.calls,
                "duration_seconds": metadata["duration_seconds"],
                "audio_base64": encoded,
                "media_type": "audio/wav",
                "usage": metadata["usage"],
            }
        finally:
            self.probe_slots.release()
