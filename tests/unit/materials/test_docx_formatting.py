from io import BytesIO

import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.documents import docx_bytes
from tests.support.pipeline import controlled_provider, settings_for, submit


@pytest.mark.parametrize("formatting", ["symbol", "style_superscript", "inherited_subscript"])
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
