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
        assert job["status"] == "needs_input"
        assert job["error"]["code"] == "vision_not_configured"
        assert job["error"]["details"]["file_id"] == asset["file_id"]
        assert job["usage"]["model_calls"] == 0 and not calls
        record = client.app.state.job_manager.store.load_record(job["job_id"])
        visuals = record["parse"]["candidates"][0]["visual_inputs"]
        assert len(visuals) == 1 and visuals[0]["locator"] == {"page": 1}
        assert visuals[0]["media_type"] == "image/jpeg" and visuals[0]["height"] > 0
        generation = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
        assert generation["answer_id"] is None and generation["material_ids"] == []
