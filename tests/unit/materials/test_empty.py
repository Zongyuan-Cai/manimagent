import httpx
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.llm.client import ChatProvider
from tests.support import wait_job
from tests.support.pipeline import settings_for, submit


def test_empty_file_requests_input_without_spending_model_calls(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(
        create_app(
            settings,
            text_provider=ChatProvider(
                settings,
                httpx.MockTransport(
                    lambda _: (_ for _ in ()).throw(AssertionError("must not call"))
                ),
            ),
        )
    ) as client:
        asset = client.post("/api/v2/files", files={"file": ("empty.txt", b"")}).json()
        job = wait_job(
            client,
            submit(client, inputs=[{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"],
        )
        assert job["status"] == "needs_input" and job["usage"]["model_calls"] == 0
