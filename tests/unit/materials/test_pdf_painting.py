import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.documents import pdf_bytes
from tests.support.pipeline import controlled_provider, settings_for, submit


@pytest.mark.parametrize("operator", ["S", "s", "f", "F", "f*", "B", "B*", "b", "b*"])
def test_every_pdf_path_painting_variant_stops_before_answer_generation(tmp_path, operator):
    settings = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(settings, controlled_provider(settings, calls))) as client:
        asset = client.post(
            "/api/v2/files",
            files={
                "file": ("diagram.pdf", pdf_bytes("Essential geometry is shown below.", operator))
            },
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "needs_review"
        assert job["usage"]["model_calls"] == 0 and not calls
