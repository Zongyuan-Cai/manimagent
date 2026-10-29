from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from manimagent.artifacts import ArtifactStore
from manimagent.config import Settings, load_settings
from manimagent.jobs import JobManager, QueueFull
from manimagent.providers import ChatProvider
from manimagent.rendering import DockerRenderer
from manimagent.schemas import GenerationRequest
from manimagent.workflow import GenerationEngine


def build_engine(settings: Settings) -> GenerationEngine:
    return GenerationEngine(
        settings, ArtifactStore(settings.runs_dir), ChatProvider(settings), DockerRenderer(settings)
    )


def create_app(settings: Settings | None = None, engine: GenerationEngine | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = engine or build_engine(settings)
    manager = JobManager(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="ManimAgent", version="0.1.0", lifespan=lifespan)
    app.state.manager = manager
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web"), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not echo invalid input, especially unpaired Unicode surrogates.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "输入格式不正确：请仅提交 text 字符串，长度为 1 至 3000 个字符",
            },
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(Path(__file__).parent / "web" / "index.html", media_type="text/html")

    @app.get("/api/health")
    def health():
        errors = engine.readiness()
        return {"ready": not errors, "issues": errors}

    @app.post("/api/jobs", status_code=202)
    def submit(request: GenerationRequest):
        errors = engine.readiness()
        if errors:
            raise HTTPException(503, detail="；".join(errors))
        try:
            manifest = manager.submit(request)
        except QueueFull as exc:
            raise HTTPException(429, detail=str(exc)) from exc
        return {"job_id": manifest.job_id, "status": manifest.status}

    def get_manifest(job_id: str):
        try:
            return engine.store.load(job_id)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(404, detail="任务不存在") from exc

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str):
        return engine.store.public(get_manifest(job_id))

    @app.get("/api/jobs/{job_id}/artifacts/{artifact_id}")
    def artifact(job_id: str, artifact_id: str, download: bool = False):
        manifest = get_manifest(job_id)
        try:
            path = engine.store.resolve(manifest, artifact_id)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise HTTPException(404, detail="产物不存在") from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            filename=path.name if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    return app
