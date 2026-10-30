from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from manimagent.config import Settings, load_settings
from manimagent.content_store import ContentError
from manimagent.providers import ChatProvider
from manimagent.text_api import UploadLimitMiddleware, text_router
from manimagent.text_jobs import TextJobManager


class WebAssets(StaticFiles):
    async def get_response(self, path: str, scope):
        first_party = path in {"app.js", "llm-settings.js", "app.css"}
        if first_party:
            scope = {
                **scope,
                "headers": [
                    (key, value)
                    for key, value in scope["headers"]
                    if key.lower() not in {b"if-none-match", b"if-modified-since"}
                ],
            }
        response = await super().get_response(path, scope)
        if first_party:
            response.headers["Cache-Control"] = "no-store"
        return response


def create_app(
    settings: Settings | None = None,
    text_provider: ChatProvider | None = None,
    animation_renderer=None,
) -> FastAPI:
    settings = settings or load_settings()
    manager = TextJobManager(settings, text_provider, animation_renderer)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="ManimAgent", version="0.3.0", lifespan=lifespan)
    app.state.text_manager = manager
    app.add_middleware(UploadLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.include_router(text_router(manager))
    web = Path(__file__).parent / "web"
    app.mount("/static", WebAssets(directory=web), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            web / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"}
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        message = (
            "LLM 配置格式无效，请检查地址、模型、密钥和数值范围"
            if request.url.path.startswith("/api/v2/llm-settings")
            else "输入格式无效，请检查必填字段、文本、范围和资源 ID"
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": message,
                    "retryable": False,
                    "details": {},
                }
            },
        )

    @app.exception_handler(ContentError)
    async def content_error(request, exc):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.error.model_dump()})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "upload_too_large" if exc.status_code == 413 else "invalid_request",
                    "message": str(exc.detail),
                    "retryable": False,
                    "details": {},
                }
            },
        )

    return app
