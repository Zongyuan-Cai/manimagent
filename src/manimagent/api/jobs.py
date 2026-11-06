from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from manimagent.jobs.manager import JobManager
from manimagent.memory.audit import memory_context, render_memory_context
from manimagent.schemas import (
    AnswerRequest,
    Job,
    ParseRequest,
    TextGenerationRequest,
)
from manimagent.video.models import VideoRequest


def router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    store = manager.store

    @router.post("/materials/parse", status_code=202, response_model=Job)
    def parse(request: ParseRequest):
        return manager.submit("parse", request)

    @router.post("/answers", status_code=202, response_model=Job)
    def answer(request: AnswerRequest):
        return manager.submit("answer", request)

    @router.post("/generations", status_code=202, response_model=Job)
    def generate(request: TextGenerationRequest):
        return manager.submit("generation", request)

    @router.get("/jobs/{job_id}", response_model=Job)
    def job(job_id: str):
        return store.load("jobs", job_id, Job)

    @router.get("/jobs/{job_id}/memory-context")
    def job_memory_context(job_id: str, download: bool = False):
        job = store.load("jobs", job_id, Job)
        record = store.load_record(job_id)
        context = memory_context(record, status=job.status)
        if download:
            return Response(
                content=render_memory_context(context),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{job_id}-memory.md"',
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )
        return context

    @router.post("/videos", status_code=202, response_model=Job)
    def create_video(request: VideoRequest):
        return manager.submit("video", request)

    return router
