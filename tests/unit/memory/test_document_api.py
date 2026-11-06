from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import Settings
from manimagent.memory.files import MAX_DOCUMENT_BYTES
from tests.support.memory import observe


def edit_value(content, before, after):
    prefix, body = content.split("## 记忆内容", 1)
    return prefix + "## 记忆内容" + body.replace(before, after, 1)


def test_document_routes_export_safe_download_and_import_a_versioned_correction(tmp_path):
    app = create_app(Settings(runs_dir=tmp_path))
    memory = app.state.job_manager.memory
    original = observe(memory)
    with TestClient(app) as client:
        listing = client.get("/api/v2/memory/documents")
        assert listing.status_code == 200, listing.text
        files = listing.json()
        assert files["error"] is None and "MEMORY.md" in files["index_path"]
        assert original.memory_id in files["index_content"]
        row = next(item for item in files["entries"] if item["memory_id"] == original.memory_id)
        assert row["status"] == "current"
        assert Path(files["root"]).is_relative_to(tmp_path)
        url = f"/api/v2/memory/documents/{original.memory_id}"
        current = client.get(url).json()
        download = client.get(url + "?download=true")
        assert download.text == current["content"]
        assert download.headers["content-type"].startswith("text/markdown")
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["cache-control"] == "no-store"
        edited = edit_value(current["content"], original.value, "先解释符号")
        result = client.post(url + "/import", json={"content": edited})
        assert result.status_code == 200, result.text
        corrected = result.json()
        assert corrected["value"] == "先解释符号"
        assert corrected["memory_id"] != original.memory_id
        assert corrected["layer"] == original.layer
        assert corrected["expires_at"] == original.expires_at
        assert corrected["independent_sessions"] == 1
        assert memory.revision() == 1
        replay = client.post(url + "/import", json={"content": edited})
        assert replay.status_code in {404, 409}, replay.text
        assert memory.revision() == 1


def test_document_disk_edits_require_explicit_import_and_survive_sync(tmp_path):
    app = create_app(Settings(runs_dir=tmp_path))
    memory = app.state.job_manager.memory
    original = observe(memory)
    with TestClient(app) as client:
        files = client.get("/api/v2/memory/documents").json()
        row = next(item for item in files["entries"] if item["memory_id"] == original.memory_id)
        path = Path(row["path"])
        if not path.is_absolute():
            path = Path(files["root"]) / path
        edited = edit_value(path.read_text(encoding="utf-8"), original.value, "先解释图例")
        path.write_text(edited, encoding="utf-8")
        synced = client.post("/api/v2/memory/documents/sync", json={}).json()
        assert (
            next(item for item in synced["entries"] if item["memory_id"] == original.memory_id)[
                "status"
            ]
            == "edited"
        )
        assert path.read_text(encoding="utf-8") == edited
        assert memory.list_entries()[0].value == original.value
        result = client.post(f"/api/v2/memory/documents/{original.memory_id}/import", json={})
        assert result.status_code == 200, result.text
        assert result.json()["value"] == "先解释图例"


def test_stale_or_forged_markdown_cannot_overwrite_an_updated_memory(tmp_path):
    app = create_app(Settings(runs_dir=tmp_path))
    memory = app.state.job_manager.memory
    original = observe(memory)
    with TestClient(app) as client:
        url = f"/api/v2/memory/documents/{original.memory_id}"
        content = client.get(url).json()["content"]
        forged = content.replace("short_term", "deep", 1)
        invalid = client.post(url + "/import", json={"content": forged})
        assert invalid.status_code in {409, 422}, invalid.text
        assert memory.revision() == 0
        confirmed = client.post(f"/api/v2/memory/{original.memory_id}/confirm", json={})
        assert confirmed.status_code == 200
        stale = client.post(
            url + "/import", json={"content": edit_value(content, original.value, "旧稿")}
        )
        assert stale.status_code == 409, stale.text
        assert memory.list_entries()[0].layer == "deep"
        assert memory.list_entries()[0].value == original.value


@pytest.mark.parametrize(
    "body", [{"content": "x" * (MAX_DOCUMENT_BYTES + 1)}, {"content": ""}, {"path": "outside.md"}]
)
def test_import_body_is_bounded_and_cannot_choose_a_server_file(tmp_path, body):
    app = create_app(Settings(runs_dir=tmp_path))
    with TestClient(app) as client:
        result = client.post("/api/v2/memory/documents/memory_invalid/import", json=body)
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "invalid_request"


def test_document_io_failure_is_explicit_in_status_without_exposing_private_details(
    tmp_path, monkeypatch
):
    app = create_app(Settings(runs_dir=tmp_path))
    memory = app.state.job_manager.memory
    with TestClient(app) as client:
        monkeypatch.setattr(
            memory,
            "sync_documents",
            lambda: {
                "root": str(tmp_path / "memory"),
                "index_path": str(tmp_path / "memory/MEMORY.md"),
                "index_content": "",
                "entries": [],
                "error": {"code": "memory_document_unavailable", "message": "文件同步失败"},
            },
        )
        response = client.get("/api/v2/memory/documents")
        assert response.status_code == 200
        assert response.json()["error"]["code"] == "memory_document_unavailable"
