from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from manimagent.budget import BudgetExceeded
from manimagent.errors import ContentError
from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ProviderError
from manimagent.llm.settings import LLMProbeRequest, LLMSettingsUpdate
from manimagent.materials.capabilities import input_capabilities
from manimagent.video.settings import TTSProbeRequest, TTSSettingsUpdate


def router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    def local_configuration(request: Request):
        # These controls belong to the local app; do not accept cross-origin changes.
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise ContentError("configuration_forbidden", "请从本机地址打开配置界面", 403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise ContentError("configuration_forbidden", "配置请求必须来自当前页面", 403)

    @router.get("/llm-settings", dependencies=[Depends(local_configuration)])
    def get_llm_settings():
        return manager.configuration()

    @router.put("/llm-settings", dependencies=[Depends(local_configuration)])
    def save_llm_settings(request: LLMSettingsUpdate):
        return manager.configure(request)

    @router.post("/llm-settings/test", dependencies=[Depends(local_configuration)])
    def test_llm_settings(request: LLMProbeRequest):
        try:
            return manager.probe_model(request)
        except ProviderError as exc:
            error = ContentError(exc.code, str(exc), 502)
            error.error.retryable = exc.retryable
            raise error from exc
        except BudgetExceeded as exc:
            raise ContentError("budget_exceeded", "连接测试达到时间或调用次数上限", 504) from exc

    @router.get("/health")
    def health():
        issues = manager.readiness()
        vision_issues = []
        if not manager.settings.model_for("material_reader").supports_vision:
            vision_issues.append("材料读取模型尚未开启图片读取，请配置支持图片的模型")
        try:
            vision_issues.extend(
                manager.settings.configuration_errors(
                    ("material_reader",),
                    manager.llm_settings.credentials.resolve,
                )
            )
        except ContentError:
            vision_issues.append("材料读取模型密钥无法读取，请在模型设置中重新填写")
        return {
            "instance_id": manager.instance.identifier,
            "ready": not issues,
            "issues": issues,
            "output_mode": "text",
            "output_modes": ["text", "both"],
            "video_issues": manager.renderer.preflight(),
            "tts": manager.tts_settings.status(manager.settings),
            "vision": {"ready": not vision_issues, "issues": vision_issues},
            "formats": [
                extension.lstrip(".")
                for item in input_capabilities(manager.settings)["formats"]
                for extension in item["extensions"]
            ],
            "limits": {
                "max_upload_bytes": manager.settings.max_upload_bytes,
                "max_parse_pages": manager.settings.max_parse_pages,
                "max_parse_chars": manager.settings.max_parse_chars,
            },
        }

    @router.get("/tts-settings", dependencies=[Depends(local_configuration)])
    def get_tts_settings():
        return JSONResponse(manager.speech_configuration(), headers={"Cache-Control": "no-store"})

    @router.put("/tts-settings", dependencies=[Depends(local_configuration)])
    def save_tts_settings(request: TTSSettingsUpdate):
        return manager.configure_speech(request)

    @router.post("/tts-settings/test", dependencies=[Depends(local_configuration)])
    def test_tts_settings(request: TTSProbeRequest):
        try:
            return JSONResponse(
                manager.probe_speech(request), headers={"Cache-Control": "no-store"}
            )
        except BudgetExceeded as exc:
            raise ContentError(
                "tts_timeout", "语音试听达到 30 秒时间上限，请稍后重试", 504
            ) from exc

    return router
