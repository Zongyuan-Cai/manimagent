from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = ""
    model: str = ""
    api_key_env: str = "MANIMAGENT_TEXT_API_KEY"
    json_mode: bool = True
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    max_output_tokens: int = Field(default=6000, ge=256, le=32000)
    auth_mode: Literal["bearer", "api_key_header", "none"] = "bearer"
    api_key_header: str = "X-API-Key"
    generate_url: str = ""
    context_window: int = Field(default=32000, ge=1024)
    thinking: Literal["default", "disabled", "enabled"] = "default"

    @field_validator("base_url", "generate_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("base_url 必须是 HTTP(S) 地址")
        if value:
            parsed = urlsplit(value)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("base_url 不能包含凭据、查询参数或片段")
        return value.rstrip("/")

    @field_validator("api_key_header")
    @classmethod
    def valid_header(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]+", value):
            raise ValueError("鉴权头名称无效")
        return value


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs_dir: Path = Path("runs/current")
    private_dir: Path | None = None
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_parse_pages: int = Field(default=100, ge=1)
    max_parse_chars: int = Field(default=200000, ge=1)
    max_archive_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    max_archive_entries: int = Field(default=2000, ge=1)
    parse_timeout: float = Field(default=60, gt=0)
    answer_context_tokens: int = Field(default=24000, ge=1024)
    answer_check: bool = True
    task_timeout: float = Field(default=900, gt=0)
    model_timeout: float = Field(default=90, gt=0)
    max_model_calls: int = Field(default=60, ge=1)
    max_total_tokens: int = Field(default=200000, ge=1000)
    max_waiting: int = Field(default=3, ge=0, le=20)
    video_timeout: float = Field(default=600, gt=0, le=600)
    video_render_timeout: float = Field(default=90, gt=0, le=90)
    video_queue_timeout: float = Field(default=120, gt=0, le=120)
    video_max_model_calls: int = Field(default=24, ge=1, le=24)
    video_max_output_tokens: int = Field(default=10000, ge=2048, le=16000)
    video_context_tokens: int = Field(default=96000, ge=8000)
    video_font: str = "auto"
    video_memory_mb: int = Field(default=2048, ge=512, le=4096)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_roles(self) -> Settings:
        if any(name not in self.models for name in self.roles.values()):
            raise ValueError("roles 引用了不存在的模型配置")
        return self

    def model_for(self, role: str) -> ModelConfig:
        fallback = "answer_checker" if role == "plan_checker" else "answer_writer"
        binding = self.roles.get(role, self.roles.get(fallback, "text"))
        return self.models.get(binding, ModelConfig())

    def configuration_errors(
        self,
        roles: tuple[str, ...] | None = None,
        credential_resolver: Callable[[str], str | None] = os.getenv,
    ) -> list[str]:
        errors = []
        for role in (
            roles
            if roles is not None
            else (("answer_writer", "answer_checker") if self.answer_check else ("answer_writer",))
        ):
            cfg = self.model_for(role)
            if (
                not cfg.model
                or not (cfg.base_url or cfg.generate_url)
                or (cfg.auth_mode != "none" and not credential_resolver(cfg.api_key_env))
            ):
                label = {"answer_writer": "回答模型", "answer_checker": "内容检查模型"}.get(
                    role, role
                )
                errors.append(f"{label} 的模型名称、接口地址或 API Key 未配置")
        return errors


def load_settings(path: str | Path | None = None) -> Settings:
    load_dotenv(Path.cwd() / ".env", override=False)
    config_path = Path(path or os.getenv("MANIMAGENT_CONFIG", "config.yaml"))
    config_path = config_path.resolve()
    if config_path.parent != Path.cwd():
        load_dotenv(config_path.parent / ".env", override=False)
    data = {}
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError("配置文件不是有效 YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 YAML 对象")

    def expand(value):
        if isinstance(value, str):
            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.getenv(m[1], ""), value)
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    data = expand(data)
    if "models" not in data:
        data["models"] = {
            name: {
                "base_url": os.getenv(f"MANIMAGENT_{name.upper()}_BASE_URL", ""),
                "model": os.getenv(f"MANIMAGENT_{name.upper()}_MODEL", ""),
                "api_key_env": f"MANIMAGENT_{name.upper()}_API_KEY",
            }
            for name in ("text",)
        }
    settings = Settings.model_validate(data)
    if not settings.runs_dir.is_absolute():
        settings.runs_dir = config_path.parent / settings.runs_dir
    settings.runs_dir = settings.runs_dir.resolve()
    if settings.private_dir is None:
        settings.private_dir = settings.runs_dir / "private"
    elif not settings.private_dir.is_absolute():
        settings.private_dir = config_path.parent / settings.private_dir
    settings.private_dir = settings.private_dir.resolve()
    return settings
