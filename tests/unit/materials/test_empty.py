import json

from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.pipeline import controlled_provider, settings_for, submit


def test_empty_file_requests_input_without_answer_model_calls(tmp_path):
    settings = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(settings, controlled_provider(settings, calls))) as client:
        asset = client.post("/api/v2/files", files={"file": ("empty.txt", b"")}).json()
        job = wait_job(
            client,
            submit(client, inputs=[{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"],
        )
        assert job["status"] == "needs_input" and job["usage"]["model_calls"] == 1
        assert len(calls) == 1 and "user_text" in json.loads(calls[0]["messages"][1]["content"])
