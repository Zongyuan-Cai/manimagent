"""MiMo's chat-completion speech contract with bounded, measured PCM output."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

import httpx

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import TTSConfig
from manimagent.errors import ContentError


class MiMoTTS:
    """One instance per video run; retries count against its separate speech limit."""

    def __init__(
        self,
        config: TTSConfig,
        credential_resolver: Callable[[str], str | None] = os.getenv,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self.credential_resolver = credential_resolver
        self.transport = transport
        self.calls = 0

    def validate_configuration(self) -> None:
        if not self.credential_resolver(self.config.api_key_env):
            raise ContentError("tts_not_configured", "MiMo 语音合成 API Key 未配置")

    async def _post(self, text: str, key: str, timeout: float, budget: Budget) -> tuple[int, bytes]:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "user", "content": self.config.style},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "wav", "voice": self.config.voice},
            "stream": False,
        }
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self.transport, timeout=timeout, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    self.config.base_url + "/chat/completions",
                    json=body,
                    headers={"api-key": key},
                ) as response:
                    if response.status_code != 200:
                        # Error bodies are untrusted and may echo credentials or input.
                        return response.status_code, b""
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        budget.remaining()
                        if len(chunks) + len(chunk) > self.config.max_response_bytes:
                            raise ContentError("tts_invalid_audio", "MiMo 语音响应超过大小限制")
                        chunks.extend(chunk)
                    return response.status_code, bytes(chunks)

    def _decode(self, payload: bytes) -> tuple[bytes, dict]:
        try:
            data = json.loads(payload)
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete synthesis")
            encoded = choice["message"]["audio"]["data"]
            if not isinstance(encoded, str):
                raise ValueError("invalid base64 value")
            raw = base64.b64decode(encoded, validate=True)
            with wave.open(io.BytesIO(raw), "rb") as audio:
                frames = audio.getnframes()
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
                sample_rate = audio.getframerate()
                if (
                    audio.getcomptype() != "NONE"
                    or channels not in (1, 2)
                    or sample_width not in (1, 2, 3, 4)
                    or not 8000 <= sample_rate <= 96000
                    or frames <= 0
                ):
                    raise ValueError("unsupported PCM format")
                duration = frames / sample_rate
                if duration > self.config.max_audio_seconds:
                    raise ValueError("audio too long")
                pcm = audio.readframes(frames)
                if len(pcm) != frames * channels * sample_width:
                    raise ValueError("truncated PCM samples")
            # Write a canonical WAV, dropping all remote metadata and trailing chunks.
            canonical = io.BytesIO()
            with wave.open(canonical, "wb") as audio:
                audio.setnchannels(channels)
                audio.setsampwidth(sample_width)
                audio.setframerate(sample_rate)
                audio.writeframes(pcm)
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            usage = {
                name: usage[name]
                for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(name), int)
                and not isinstance(usage[name], bool)
                and usage[name] >= 0
            }
            return canonical.getvalue(), {
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "frames": frames,
                "channels": channels,
                "sample_width": sample_width,
                "model": self.config.model,
                "voice": self.config.voice,
                "usage": usage,
            }
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            wave.Error,
            EOFError,
            binascii.Error,
        ):
            raise ContentError(
                "tts_invalid_audio", "MiMo 未返回完整、有效且符合时长限制的 PCM WAV 音频"
            ) from None

    def synthesize(self, text: str, output_path: Path, budget: Budget) -> dict:
        """Preserve the original narration; timings come from decoded sample counts."""
        self.validate_configuration()
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ContentError("tts_invalid_text", "旁白必须为非空且不超过2000字符的文本")
        key = self.credential_resolver(self.config.api_key_env)
        if not key:
            raise ContentError("tts_not_configured", "MiMo 语音合成 API Key 未配置")
        error = ContentError("tts_unavailable", "MiMo 语音服务暂不可用")
        for attempt in range(self.config.max_retries + 1):
            timeout = min(budget.remaining(), self.config.timeout)
            if self.calls >= self.config.max_calls:
                raise BudgetExceeded("已达到语音合成调用次数上限")
            self.calls += 1
            try:
                status, payload = asyncio.run(self._post(text, key, timeout, budget))
            except (TimeoutError, httpx.TimeoutException):
                error = ContentError("tts_timeout", "MiMo 语音合成超时")
                continue
            except httpx.HTTPError:
                error = ContentError("tts_unavailable", "MiMo 语音服务连接失败")
                continue
            budget.remaining()
            if status != 200:
                code = {
                    401: "tts_auth_error",
                    403: "tts_auth_error",
                    402: "tts_quota_exceeded",
                    429: "tts_rate_limited",
                    408: "tts_timeout",
                }.get(status, "tts_unavailable")
                error = ContentError(code, f"MiMo 语音接口返回 HTTP {status}")
                if status not in (408, 429) and status < 500:
                    raise error
                continue
            raw, metadata = self._decode(payload)
            budget.remaining()
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=output_path.parent, prefix=".tts-", suffix=".wav", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                budget.remaining()
                temporary.replace(output_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return {"path": str(output_path), "attempts": attempt + 1, **metadata}
        budget.remaining()
        raise error from None
