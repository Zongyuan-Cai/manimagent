"""Markdown is a derived export and never a learning input."""

import pytest

from manimagent.errors import ContentError
from manimagent.memory.store import MemoryStore
from tests.support.memory import observe_proposals as observe


def test_export_contains_canonical_value_and_original_evidence(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    e = observe(store)[0]
    content = store.read_document(e.memory_id)
    assert "detailed" in content and "请详细讲解" in content and "仅供查看和导出" in content
    assert store.sync_documents()["error"] is None


def test_edit_cannot_change_memory_or_import(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    e = observe(store)[0]
    path = store.documents.path(e.memory_id)
    path.write_text("edited local file", encoding="utf-8")
    result = store.sync_documents()
    assert result["entries"][0]["status"] == "edited"
    assert path.read_text(encoding="utf-8") == "edited local file"
    with pytest.raises(ContentError, match="仅用于导出"):
        store.import_document(e.memory_id, "concise")
    assert store.list_entries()[0].value == "detailed"


def test_deleted_export_removed_but_manual_copy_is_preserved(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    e = observe(store)[0]
    path = store.documents.path(e.memory_id)
    store.delete(e.memory_id)
    assert not path.exists()
    assert not store.list_entries()


def test_path_traversal_is_rejected(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ContentError):
        store.read_document("../outside")


def test_file_export_failure_does_not_rollback_committed_memory(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.sqlite3")

    def fail(*args):
        raise OSError("controlled export failure")

    monkeypatch.setattr("manimagent.memory.files.write_text", fail)
    e = observe(store)[0]
    assert store.list_entries()[0].memory_id == e.memory_id
    assert store.sync_documents()["error"]
