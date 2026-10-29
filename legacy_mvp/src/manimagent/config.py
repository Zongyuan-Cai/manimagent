from __future__ import annotations

import os
import re
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

    @field_validator("base_url")
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


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs_dir: Path = Path("runs")
    renderer_image: str = "manimagent-renderer:0.1.0"
    target_seconds: int = Field(default=60, ge=10, le=120)
    max_scenes: int = Field(default=4, ge=1, le=4)
    max_execution_repairs: int = Field(default=2, ge=0, le=5)
    max_visual_revisions: int = Field(default=2, ge=0, le=5)
    max_candidates: int = Field(default=5, ge=1, le=11)
    render_timeout: float = Field(default=180, gt=0)
    scene_timeout: float = Field(default=300, gt=0)
    task_timeout: float = Field(default=900, gt=0)
    model_timeout: float = Field(default=90, gt=0)
    max_model_calls: int = Field(default=60, ge=1)
    max_total_tokens: int = Field(default=200000, ge=1000)
    max_waiting: int = Field(default=3, ge=0, le=20)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_roles(self) -> Settings:
        if any(name not in self.models for name in self.roles.values()):
            raise ValueError("roles 引用了不存在的模型配置")
        return self

    def model_for(self, role: str) -> ModelConfig:
        default = "vision" if role == "vision_checker" else "text"
        return self.models.get(self.roles.get(role, default), ModelConfig())

    def configuration_errors(self) -> list[str]:
        errors = []
        for role in (
            "planner",
            "plan_checker",
            "coder",
            "execution_repair",
            "visual_reviser",
            "vision_checker",
        ):
            cfg = self.model_for(role)
            if not cfg.model or not cfg.base_url or not os.getenv(cfg.api_key_env):
                errors.append(f"{role} 的模型、接口地址或环境变量 {cfg.api_key_env} 未配置")
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
            for name in ("text", "vision")
        }
    settings = Settings.model_validate(data)
    if not settings.runs_dir.is_absolute():
        settings.runs_dir = config_path.parent / settings.runs_dir
    settings.runs_dir = settings.runs_dir.resolve()
    return settings
