from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from manimagent.answers import AnswerService
from manimagent.budget import BudgetExceeded
from manimagent.content_store import ContentError
from manimagent.llm_settings import LLMProbeRequest, LLMSettingsUpdate
from manimagent.markdown_view import render_markdown
from manimagent.providers import ChatProvider, ProviderError
from manimagent.schemas import (
    Answer,
    AnswerRequest,
    AnswerRevisionRequest,
    FileAsset,
    Generation,
    Job,
    MaterialIR,
    ParseRequest,
    TextGenerationRequest,
)
from manimagent.text_jobs import TextJobManager


class UploadLimitMiddleware:
    """Bound multipart bytes while receiving, before Starlette spools the entire upload."""

    def __init__(self, app, max_bytes: int):
        self.app, self.limit = app, max_bytes + 65536

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v2/files" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > self.limit:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "upload_too_large",
                        "message": "文件请求超过上传上限",
                        "retryable": False,
                        "details": {},
                    }
                },
            )
            return await response(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise HTTPException(413, "文件请求超过上传上限")
            return message

        return await self.app(scope, limited_receive, send)


def text_router(manager: TextJobManager) -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    store = manager.store

    def local_configuration(request: Request):
        # These controls belong to the local app; do not accept cross-origin changes.
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise ContentError("configuration_forbidden", "请从本机地址打开配置界面", 403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise ContentError("configuration_forbidden", "配置请求必须来自当前页面", 403)

    @router.get("/llm-settings", dependencies=[Depends(local_configuration)])
    def get_llm_settings():
        with manager.lock:
            return manager.llm_settings.view(manager.settings)

    @router.put("/llm-settings", dependencies=[Depends(local_configuration)])
    def save_llm_settings(request: LLMSettingsUpdate):
        with manager.lock:
            manager.settings = manager.llm_settings.update(request, manager.settings)
            return manager.llm_settings.view(manager.settings)

    @router.post("/llm-settings/test", dependencies=[Depends(local_configuration)])
    def test_llm_settings(request: LLMProbeRequest):
        with manager.lock:
            snapshot = manager.settings.model_copy(deep=True)
        try:
            return manager.llm_settings.probe(
                request, snapshot, manager.provider.transport if manager.provider else None
            )
        except ProviderError as exc:
            error = ContentError(exc.code, str(exc), 502)
            error.error.retryable = exc.retryable
            raise error from exc
        except BudgetExceeded as exc:
            raise ContentError("budget_exceeded", "连接测试达到时间或调用次数上限", 504) from exc

    @router.get("/health")
    def health():
        issues = manager.readiness()
        return {
            "ready": not issues,
            "issues": issues,
            "output_mode": "text",
            "formats": ["txt", "md", "pdf", "docx"],
            "limits": {
                "max_upload_bytes": manager.settings.max_upload_bytes,
                "max_parse_pages": manager.settings.max_parse_pages,
                "max_parse_chars": manager.settings.max_parse_chars,
            },
        }

    @router.post(
        "/files",
        status_code=201,
        response_model=FileAsset,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["file"],
                            "properties": {"file": {"type": "string", "format": "binary"}},
                        }
                    }
                },
            }
        },
    )
    async def upload(request: Request):
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if len(form) != 1 or not isinstance(file, UploadFile):
                raise ContentError("invalid_request", "请以 multipart/form-data 提交单个 file 字段")
            return await run_in_threadpool(
                store.put_file, file.file, file.filename or "material.txt", file.content_type or ""
            )

    @router.get("/files/{file_id}", response_model=FileAsset)
    def get_file(file_id: str):
        return store.load("files", file_id, FileAsset)

    @router.get("/files/{file_id}/content")
    def file_content(file_id: str):
        asset = store.verify_file(file_id)
        # User-controlled HTML or text is always downloaded; no active document executes in origin.
        return FileResponse(
            store.file_path(file_id),
            media_type=asset.media_type,
            filename=asset.filename,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.post("/materials/parse", status_code=202, response_model=Job)
    def parse(request: ParseRequest):
        return manager.submit("parse", request)

    @router.get("/materials/{material_id}", response_model=MaterialIR)
    def material(material_id: str):
        return store.load("materials", material_id, MaterialIR)

    @router.post("/answers", status_code=202, response_model=Job)
    def answer(request: AnswerRequest):
        return manager.submit("answer", request)

    @router.post("/answers/revisions", status_code=201, response_model=Answer)
    def revision(request: AnswerRevisionRequest):
        return AnswerService(store, ChatProvider(manager.settings)).revise(request)

    @router.get("/answers/{answer_id}", response_model=Answer)
    def get_answer(answer_id: str):
        return store.load("answers", answer_id, Answer)

    @router.get("/answers/{answer_id}/markdown")
    def markdown(answer_id: str, download: bool = False):
        result = store.load("answers", answer_id, Answer)
        headers = {"X-Content-Type-Options": "nosniff"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{result.answer_id}.md"'
        return Response(
            content=result.markdown, media_type="text/markdown; charset=utf-8", headers=headers
        )

    @router.get("/answers/{answer_id}/html")
    def rendered_markdown(answer_id: str):
        result = store.load("answers", answer_id, Answer)
        # Raw HTML, scripts and unsafe link protocols in model/user output stay inert.
        html = render_markdown(result.markdown)
        return Response(
            content=html, media_type="text/html", headers={"X-Content-Type-Options": "nosniff"}
        )

    @router.post("/generations", status_code=202, response_model=Job)
    def generate(request: TextGenerationRequest):
        return manager.submit("generation", request)

    @router.get("/generations/{generation_id}", response_model=Generation)
    def generation(generation_id: str):
        return store.load("generations", generation_id, Generation)

    @router.get("/jobs/{job_id}", response_model=Job)
    def job(job_id: str):
        return store.load("jobs", job_id, Job)

    return router
