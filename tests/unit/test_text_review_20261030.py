from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier

import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from scripts.review_live_text import docx_bytes, pdf_bytes
from tests.integration.test_pipeline import controlled_provider, settings_for, submit
from tests.support import wait_job


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


@pytest.mark.parametrize(
    "formatting", ["superscript", "subscript", "symbol", "style_superscript", "inherited_subscript"]
)
def test_docx_math_run_formatting_never_silently_disappears(tmp_path, formatting):
    settings = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(settings, controlled_provider(settings, calls))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("math.docx", docx_bytes("", formatting))}
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "needs_review"
        assert job["usage"]["model_calls"] == 0 and not calls


def test_unused_docx_math_style_does_not_block_plain_text(tmp_path):
    settings = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(settings, controlled_provider(settings, calls))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("plain.docx", docx_bytes("", "unused_style"))}
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "completed"
        assert job["usage"]["model_calls"] == 2


@pytest.mark.parametrize("boolean_value", ["1", "true", "on"])
def test_default_docx_style_math_is_not_lost(tmp_path, boolean_value):
    document = Document()
    document.styles["Normal"].font.superscript = True
    document.styles["Normal"].element.set(qn("w:default"), boolean_value)
    document.add_paragraph("2")
    stream = BytesIO()
    document.save(stream)
    settings = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(settings, controlled_provider(settings, calls))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("power.docx", stream.getvalue())}
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "needs_review"
        assert job["usage"]["model_calls"] == 0 and not calls


def test_polling_and_publication_across_store_instances_never_lose_json(tmp_path):
    first, second = (
        ContentStore(Settings(runs_dir=tmp_path)),
        ContentStore(Settings(runs_dir=tmp_path)),
    )
    identifier = new_id("job")
    first.save("records", identifier, {"revision": 0})
    barrier = Barrier(2)

    def publish():
        barrier.wait()
        for i in range(1, 301):
            first.save("records", identifier, {"revision": i})

    def poll():
        barrier.wait()
        for _ in range(1500):
            assert 0 <= second.load_record(identifier)["revision"] <= 300

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish), pool.submit(poll)]
        for future in futures:
            future.result()
    assert second.load_record(identifier)["revision"] == 300
