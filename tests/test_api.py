from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.artifacts import ArtifactStore
from manimagent.config import Settings
from manimagent.jobs import JobManager, QueueFull
from manimagent.schemas import GenerationRequest
from tests.helpers import make_engine


def wait_for(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] not in {"queued", "running"}:
            return data
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_web_submission_status_and_artifact_download(tmp_path):
    engine = make_engine(tmp_path)
    with TestClient(create_app(engine.settings, engine)) as client:
        assert client.get("/").status_code == 200
        assert 'id="text"' in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/api/health").json()["ready"]
        response = client.post("/api/jobs", json={"text": "解释向量投影"})
        assert response.status_code == 202
        data = wait_for(client, response.json()["job_id"])
        assert data["status"] == "completed"
        assert str(tmp_path) not in str(data)
        final = next(a for a in data["artifacts"] if a["kind"] == "final")
        download = client.get(final["url"] + "?download=true")
        assert (
            download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        )
        ranged = client.get(final["url"], headers={"Range": "bytes=0-3"})
        assert ranged.status_code == 206 and len(ranged.content) == 4
        assert client.get(f"/api/jobs/{data['job_id']}/artifacts/missing").status_code == 404
        assert client.get("/api/jobs/not-a-job").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": 10},
        {"text": ""},
        {"text": "x" * 3001},
        {"text": "向量", "file": "paper.pdf"},
        {"text": None},
    ],
)
def test_api_rejects_invalid_input_before_job_creation(tmp_path, body):
    engine = make_engine(tmp_path)
    with TestClient(create_app(engine.settings, engine)) as client:
        assert client.post("/api/jobs", json=body).status_code == 422
        assert not list(engine.store.root.glob("*/request.json"))


def test_missing_model_configuration_returns_503_without_creating_job(tmp_path, monkeypatch):
    monkeypatch.setattr("manimagent.rendering.DockerRenderer.preflight", lambda self: [])
    settings = Settings(runs_dir=tmp_path / "runs")
    with TestClient(create_app(settings)) as client:
        assert not client.get("/api/health").json()["ready"]
        response = client.post("/api/jobs", json={"text": "向量投影"})
        assert response.status_code == 503
        assert not list(settings.runs_dir.glob("*/request.json"))


def test_invalid_unicode_is_a_validation_error_not_a_server_error(tmp_path):
    engine = make_engine(tmp_path)
    with TestClient(create_app(engine.settings, engine)) as client:
        response = client.post(
            "/api/jobs", content=b'{"text":"\\ud800"}', headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert not list(engine.store.root.glob("*/request.json"))


class BlockingEngine:
    def __init__(self, directory):
        self.settings = Settings(runs_dir=directory, max_waiting=1)
        self.store = ArtifactStore(directory)
        self.started = threading.Event()
        self.release = threading.Event()

    def readiness(self):
        return []

    def execute(self, job_id):
        self.started.set()
        self.release.wait(5)
        manifest = self.store.load(job_id)
        manifest.status = "failed"
        self.store.save(manifest)

    def stop(self):
        self.release.set()


def test_queue_capacity_and_slots_are_released(tmp_path):
    engine = BlockingEngine(tmp_path)
    manager = JobManager(engine)
    manager.start()
    request = GenerationRequest(text="向量")
    try:
        manager.submit(request)
        assert engine.started.wait(2)
        manager.submit(request)
        with pytest.raises(QueueFull):
            manager.submit(request)
        engine.release.set()
        manager.queue.join()
        manager.submit(request)
        manager.queue.join()
    finally:
        manager.stop()


def test_api_queue_full_is_429(tmp_path):
    engine = BlockingEngine(tmp_path)
    with TestClient(create_app(engine.settings, engine)) as client:
        assert client.post("/api/jobs", json={"text": "1"}).status_code == 202
        assert engine.started.wait(2)
        assert client.post("/api/jobs", json={"text": "2"}).status_code == 202
        assert client.post("/api/jobs", json={"text": "3"}).status_code == 429


def test_startup_marks_existing_unfinished_job_interrupted(tmp_path):
    engine = make_engine(tmp_path)
    manifest = engine.store.create(GenerationRequest(text="上次任务"))
    with TestClient(create_app(engine.settings, engine)) as client:
        assert client.get(f"/api/jobs/{manifest.job_id}").json()["status"] == "interrupted"
