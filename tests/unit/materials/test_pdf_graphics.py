import io

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from manimagent.api import create_app
from manimagent.config import Settings
from tests.support import wait_job
from tests.support.documents import pdf_pages as pdf_bytes


def test_pdf_content_stream_without_dictionary_entries_still_detects_vector_graphics(tmp_path):
    reader = PdfReader(io.BytesIO(pdf_bytes(["Triangle diagram is essential."])))
    writer = PdfWriter()
    writer.append(reader)
    content = DecodedStreamObject()
    content.set_data(
        writer.pages[0].get_contents().get_data() + b"\n100 100 m 200 100 l 150 200 l h S\n"
    )
    writer.pages[0][NameObject("/Contents")] = writer._add_object(content)
    data = io.BytesIO()
    writer.write(data)
    assert not bool(writer.pages[0].get_contents())
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("diagram.pdf", data.getvalue())}
        ).json()
        request = client.post(
            "/api/v2/materials/parse",
            json={"inputs": [{"type": "file", "file_id": asset["file_id"]}]},
        )
        job = wait_job(client, request.json()["job_id"])
        assert job["status"] == "needs_review" and job["usage"]["model_calls"] == 0
