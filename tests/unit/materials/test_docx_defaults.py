from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.pipeline import controlled_provider, settings_for, submit


@pytest.mark.parametrize("kind", ["superscript", "subscript"])
def test_docdefaults_math_blocks_real_parser_and_preserves_original(tmp_path, kind):
    document = Document()
    defaults = document.styles.element.find(qn("w:docDefaults"))
    properties = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    align = OxmlElement("w:vertAlign")
    align.set(qn("w:val"), kind)
    properties.append(align)
    paragraph = document.add_paragraph()
    for text, baseline in [("x", True), ("2", False), ("=4", True)]:
        run = paragraph.add_run(text)
        if baseline:
            align = OxmlElement("w:vertAlign")
            align.set(qn("w:val"), "baseline")
            run._r.get_or_add_rPr().append(align)
    stream = BytesIO()
    document.save(stream)
    cfg = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(cfg, controlled_provider(cfg, calls))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("math.docx", stream.getvalue())}
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "needs_review" and not calls
        assert job["usage"]["model_calls"] == 0
        assert client.get(f"/api/v2/files/{asset['file_id']}/content").content == stream.getvalue()
