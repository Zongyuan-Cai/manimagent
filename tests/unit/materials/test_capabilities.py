from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.materials.capabilities import UPLOAD_EXTENSIONS, input_capabilities
from manimagent.materials.service import MaterialService
from manimagent.schemas import ParseRequest, Usage


def test_capabilities_reflect_server_limits_and_the_upload_allowlist(tmp_path):
    settings = Settings(
        runs_dir=tmp_path, max_upload_bytes=900, max_parse_pages=3, max_parse_chars=1000
    )
    data = input_capabilities(settings)
    assert data["limits"] == {
        "max_upload_bytes": 900,
        "max_parse_pages": 3,
        "max_parse_chars": 1000,
        "max_visual_pages": 12,
        "max_inputs": 20,
        "max_text_chars": 200000,
    }
    assert {ext for item in data["formats"] for ext in item["extensions"]} == UPLOAD_EXTENSIONS
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v2/input-capabilities").json() == data


def test_upload_error_identifies_the_file_without_publishing_it(tmp_path):
    store = ContentStore(tmp_path, 20)
    with pytest.raises(ContentError) as caught:
        store.put_file(BytesIO(b"executable"), "folder/program.exe")
    assert caught.value.error.details == {"filename": "program.exe"}
    assert "program.exe" in caught.value.error.message
    assert not list((tmp_path / "files").glob("*"))


@pytest.mark.parametrize("issue", ["invalid_selection", "content_uncertain"])
def test_parser_failure_identifies_the_problem_file_and_keeps_its_recovery_state(
    tmp_path, monkeypatch, issue
):
    settings = Settings(runs_dir=tmp_path)
    store = ContentStore(tmp_path, settings.max_upload_bytes)
    asset = store.put_file(BytesIO(b"%PDF-1.7"), "chapter.pdf")

    def parse(*args):
        if issue == "invalid_selection":
            raise ContentError(issue, "页码超出实际范围", state="needs_input")
        return {
            "coverage": {},
            "blocks": [],
            "visuals": [],
            "issues": ["第 2 页是扫描页，请提供可读文本"],
        }

    monkeypatch.setattr("manimagent.materials.service.parse_isolated", parse)
    request = ParseRequest(inputs=[{"type": "file", "file_id": asset.file_id}])
    with pytest.raises(ContentError) as caught:
        MaterialService(store, settings).parse(request, Budget(settings, Usage()), {})
    error = caught.value
    assert error.error.code == issue
    assert error.error.message.startswith("chapter.pdf：")
    assert error.error.details == {
        "filename": "chapter.pdf",
        "file_id": asset.file_id,
        "input_index": 0,
    }
    assert error.state == ("needs_input" if issue == "invalid_selection" else "needs_review")
