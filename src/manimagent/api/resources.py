from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from manimagent.answers.markdown import render_markdown
from manimagent.answers.service import AnswerService
from manimagent.errors import ContentError
from manimagent.jobs.manager import JobManager
from manimagent.llm.client import ChatProvider
from manimagent.materials.capabilities import input_capabilities
from manimagent.schemas import (
    Answer,
    AnswerRevisionRequest,
    FileAsset,
    Generation,
    MaterialIR,
)
from manimagent.video.models import VideoPlan, VideoResult


def router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    store = manager.store

    @router.get("/input-capabilities")
    def capabilities():
        return input_capabilities(manager.settings)

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
                store.put_file, file.file, file.filename or "material.txt"
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

    @router.get("/materials/{material_id}", response_model=MaterialIR)
    def material(material_id: str):
        return store.load("materials", material_id, MaterialIR)

    @router.post("/answers/revisions", status_code=201, response_model=Answer)
    def revision(request: AnswerRevisionRequest):
        return AnswerService(store, ChatProvider(manager.settings), manager.settings).revise(
            request
        )

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

    @router.get("/generations/{generation_id}", response_model=Generation)
    def generation(generation_id: str):
        return store.load("generations", generation_id, Generation)

    @router.get("/videos/{video_id}", response_model=VideoResult)
    def get_video(video_id: str):
        return store.load("videos", video_id, VideoResult)

    @router.get("/videos/{video_id}/plan", response_model=VideoPlan)
    def get_video_plan(video_id: str):
        video = store.load("videos", video_id, VideoResult)
        return store.load("video_plans", video.plan_id, VideoPlan)

    @router.get("/videos/{video_id}/content")
    def video_content(video_id: str, download: bool = False):
        video = store.load("videos", video_id, VideoResult)
        asset = store.verify_file(video.video_asset_id)
        return FileResponse(
            store.file_path(asset.file_id),
            media_type="video/mp4",
            filename="teaching.mp4" if download else None,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.get("/videos/{video_id}/frames/{index}")
    def frame_content(video_id: str, index: int):
        video = store.load("videos", video_id, VideoResult)
        if not 0 <= index < len(video.frame_mapping):
            raise ContentError("invalid_reference", "关键帧不存在", 404)
        asset = store.verify_file(video.frame_mapping[index]["asset_id"])
        return FileResponse(
            store.file_path(asset.file_id),
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return router
