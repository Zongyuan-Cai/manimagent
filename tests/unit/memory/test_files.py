from concurrent.futures import ThreadPoolExecutor

import pytest

from manimagent.errors import ContentError
from manimagent.memory import files
from manimagent.memory.files import render_document
from manimagent.memory.store import MemoryStore
from tests.support.memory import Clock, observe


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3", clock=Clock())


def edit(content, old="先画图", new="先列公式"):
    return content.replace("## 记忆内容\n\n" + old, "## 记忆内容\n\n" + new)


def item(status, memory_id):
    return next(item for item in status["entries"] if item["memory_id"] == memory_id)


def test_observation_publishes_readable_index_metadata_and_evidence_without_rewriting(store):
    entry = observe(store)
    path = store.documents.path(entry.memory_id)
    before = (path.stat().st_mtime_ns, store.documents.index_path.stat().st_mtime_ns)
    status = store.sync_documents()
    assert status["error"] is None
    assert item(status, entry.memory_id)["status"] == "current"
    text = store.read_document(entry.memory_id)
    assert "我希望先画图。" in text and '"layer": "short_term"' in text
    assert f"entries/{entry.memory_id}.md" in status["index_content"]
    assert before == (path.stat().st_mtime_ns, store.documents.index_path.stat().st_mtime_ns)


def test_confirm_and_reinforcement_refresh_clean_files_but_keep_local_drafts(store):
    first = observe(store)
    store.confirm(first.memory_id)
    assert '"layer": "deep"' in store.read_document(first.memory_id)
    draft = edit(store.read_document(first.memory_id))
    path = store.documents.path(first.memory_id)
    path.write_text(draft, encoding="utf-8", newline="")
    assert item(store.sync_documents(), first.memory_id)["status"] == "edited"
    observe(store, source="later")
    assert store.read_document(first.memory_id) == draft
    assert item(store.sync_documents(), first.memory_id)["status"] == "stale"
    with pytest.raises(ContentError) as exc:
        store.import_document(first.memory_id)
    assert exc.value.http_status == 409
    assert store.list_entries()[0].value == first.value


def test_import_is_an_atomic_correction_preserving_layer_expiry_and_fencing_background(store):
    first = observe(store)
    observe(store, source="second", session="s2")
    current = store.list_entries()[0]
    revision = store.revision()
    draft = edit(store.read_document(first.memory_id))
    store.documents.path(first.memory_id).write_text(draft, encoding="utf-8", newline="")
    changed = store.import_document(first.memory_id)
    assert changed.memory_id != first.memory_id
    assert changed.layer == current.layer and changed.expires_at == current.expires_at
    assert changed.value == "先列公式" and changed.independent_sessions == 1
    assert changed.evidence[0].kind == "correction"
    assert store.revision() == revision + 1
    assert not store.documents.path(first.memory_id).exists()
    assert store.read_document(changed.memory_id) == render_document(changed)
    assert store.replay(session_id="s1", source_id="r1", source_text="我希望先画图。") == []
    with pytest.raises(ContentError, match="修改"):
        store.observe(
            [], session_id="s", source_id="late", source_text="旧请求", expected_revision=revision
        )


@pytest.mark.parametrize("replacement", ["", "x" * 301, "bad\x00value"])
def test_invalid_edited_values_do_not_change_database_or_manual_revision(store, replacement):
    entry = observe(store)
    revision = store.revision()
    with pytest.raises(ContentError) as exc:
        store.import_document(
            entry.memory_id, edit(store.read_document(entry.memory_id), new=replacement)
        )
    assert exc.value.error.code == "memory_document_invalid"
    assert store.revision() == revision
    assert store.list_entries() == [entry]


@pytest.mark.parametrize(
    "old,new",
    [
        ('"short_term"', '"deep"'),
        ('"视觉偏好"', '"profile.background"'),
        ("我希望先画图。", "伪造证据"),
    ],
)
def test_metadata_and_evidence_edits_cannot_forge_promotion_or_support(store, old, new):
    entry = observe(store)
    content = store.read_document(entry.memory_id).replace(old, new)
    with pytest.raises(ContentError) as exc:
        store.import_document(entry.memory_id, content)
    assert exc.value.error.code == "memory_document_invalid"
    assert store.list_entries() == [entry]


def test_deleted_and_expired_memories_remove_only_untouched_mirrors(store):
    clean = observe(store)
    edited = observe(store, source="edited", field="其他", value="慢一点")
    draft = edit(store.read_document(edited.memory_id), old="慢一点", new="快一点")
    store.documents.path(edited.memory_id).write_text(draft, encoding="utf-8", newline="")
    store.delete(clean.memory_id)
    assert not store.documents.path(clean.memory_id).exists()
    store.clock.advance(days=15)
    store.cleanup()
    assert store.read_document(edited.memory_id) == draft
    assert item(store.sync_documents(), edited.memory_id)["status"] == "deleted"
    with pytest.raises(ContentError) as exc:
        store.import_document(edited.memory_id)
    assert exc.value.error.code == "memory_document_conflict"
    assert not store.list_entries()


def test_export_failure_keeps_committed_memory_and_is_visible_then_recovers(store, monkeypatch):
    def fail(*args):
        raise OSError("controlled disk failure")

    with monkeypatch.context() as fault:
        fault.setattr(files, "write_text", fail)
        entry = observe(store)
        assert store.list_entries() == [entry]
        assert store.sync_documents()["error"]["code"] == "memory_document_unavailable"
    assert store.sync_documents()["error"] is None
    assert store.read_document(entry.memory_id) == render_document(entry)


def test_missing_manifest_keeps_existing_files_and_does_not_touch_task_snapshots(store):
    entry = observe(store)
    path = store.documents.path(entry.memory_id)
    draft = edit(store.read_document(entry.memory_id))
    path.write_text(draft, encoding="utf-8", newline="")
    store.documents.manifest_path.unlink()
    snapshots = store.documents.root / "snapshots"
    snapshots.mkdir()
    snapshot = snapshots / "job_x.md"
    snapshot.write_text("frozen", encoding="utf-8")
    reopened = MemoryStore(store.path, clock=store.clock)
    assert item(reopened.sync_documents(), entry.memory_id)["status"] == "edited"
    assert reopened.read_document(entry.memory_id) == draft
    assert snapshot.read_text(encoding="utf-8") == "frozen"


def test_import_does_not_erase_a_newer_editor_draft(store):
    entry = observe(store)
    original = store.read_document(entry.memory_id)
    content = edit(original)
    newer = edit(original, new="先举例子")
    store.documents.path(entry.memory_id).write_text(newer, encoding="utf-8", newline="")
    corrected = store.import_document(entry.memory_id, content)
    assert corrected.value == "先列公式"
    assert store.read_document(entry.memory_id) == newer
    assert item(store.sync_documents(), entry.memory_id)["status"] == "deleted"


def test_noop_import_accepts_native_crlf_without_resetting_support(store):
    entry = observe(store)
    observe(store, source="second", session="s2")
    original = store.list_entries()[0]
    result = store.import_document(
        entry.memory_id, store.read_document(entry.memory_id).replace("\n", "\r\n")
    )
    assert result == original and store.revision() == 0


def test_concurrent_draft_imports_allow_only_one_committed_correction(store):
    entry = observe(store)
    original = store.read_document(entry.memory_id)
    drafts = [edit(original, new=value) for value in ["A", "B"]]

    def apply(content):
        try:
            return store.import_document(entry.memory_id, content)
        except ContentError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, drafts))
    assert sum(isinstance(result, ContentError) for result in results) == 1
    assert len(store.list_entries()) == 1 and store.revision() == 1


@pytest.mark.parametrize(
    "memory_id", ["../outside", "memory_../outside", "other", "memory_" + "a" * 32 + "/x"]
)
def test_document_ids_cannot_escape_the_entry_directory(store, memory_id):
    with pytest.raises(ContentError, match="标识"):
        store.read_document(memory_id)


def test_import_byte_limit_applies_before_database_mutation(store):
    entry = observe(store)
    with pytest.raises(ContentError, match="512 KiB"):
        store.import_document(entry.memory_id, "中文" * files.MAX_DOCUMENT_BYTES)
    assert store.revision() == 0


def test_failed_import_transaction_does_not_publish_or_consume_the_draft(store, monkeypatch):
    entry = observe(store)
    content = edit(store.read_document(entry.memory_id))
    store.documents.path(entry.memory_id).write_text(content, encoding="utf-8", newline="")
    original = store._save

    def fail_new(db, item):
        original(db, item)
        if item.memory_id != entry.memory_id:
            raise OSError("injected write failure")

    monkeypatch.setattr(store, "_save", fail_new)
    with pytest.raises(OSError, match="injected"):
        store.import_document(entry.memory_id)
    assert store.revision() == 0 and store.list_entries() == [entry]
    assert store.read_document(entry.memory_id) == content


def test_partial_publication_without_manifest_is_preserved_until_explicit_import(
    store, monkeypatch
):
    def fail(*args):
        raise OSError("manifest write interrupted")

    with monkeypatch.context() as fault:
        fault.setattr(files, "write_json", fail)
        entry = observe(store)
        assert store.sync_documents()["error"] is not None
    assert store.read_document(entry.memory_id) == render_document(entry)
    # A file without its manifest cannot be proven to be an untouched export.
    assert item(store.sync_documents(), entry.memory_id)["status"] == "edited"
    assert store.import_document(entry.memory_id) == entry
    assert item(store.sync_documents(), entry.memory_id)["status"] == "current"


def test_manifest_committed_before_index_failure_recovers_without_changing_entries(
    store, monkeypatch
):
    original = files.write_text

    def fail_index(path, content):
        if path.name == "MEMORY.md":
            raise OSError("index write interrupted")
        return original(path, content)

    with monkeypatch.context() as fault:
        fault.setattr(files, "write_text", fail_index)
        entry = observe(store)
        assert store.sync_documents()["error"] is not None
    assert item(store.sync_documents(), entry.memory_id)["status"] == "current"
    assert store.read_document(entry.memory_id) == render_document(entry)


def test_largest_configured_evidence_collection_remains_readable_and_importable(tmp_path):
    from manimagent.memory.models import MemoryCandidate, MemoryPolicy

    store = MemoryStore(tmp_path / "memory.sqlite3", MemoryPolicy(max_evidence_per_entry=100))
    quote = "数" * 500
    for index in range(100):
        entry = store.observe(
            [MemoryCandidate(field="细节", value="详细", evidence=quote)],
            session_id="s",
            source_id=str(index),
            source_text=quote,
        )[0]
    content = store.read_document(entry.memory_id)
    assert len(content.encode()) > 128 * 1024
    assert store.import_document(entry.memory_id, content) == entry


def test_import_cleanup_failure_does_not_hide_commit_and_can_retry(store, monkeypatch):
    from pathlib import Path

    entry = observe(store)
    draft = edit(store.read_document(entry.memory_id))
    path = store.documents.path(entry.memory_id)
    path.write_text(draft, encoding="utf-8", newline="")
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path.name == entry.memory_id + ".md":
            raise PermissionError("controlled editor lock")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "unlink", locked)
        changed = store.import_document(entry.memory_id)
        assert changed.value == "先列公式"
        assert store.sync_documents()["error"] is not None
        assert path.exists()
    assert store.sync_documents()["error"] is None
    assert not path.exists()
    assert store.read_document(changed.memory_id) == render_document(changed)


def test_sync_preserves_editor_save_while_new_document_is_being_rendered(store, monkeypatch):
    entry = observe(store)
    original = store.read_document(entry.memory_id)
    draft = edit(original)
    renderer = files.render_document

    def edit_while_rendering(item):
        content = renderer(item)
        store.documents.path(item.memory_id).write_text(draft, encoding="utf-8", newline="")
        return content

    monkeypatch.setattr(files, "render_document", edit_while_rendering)
    store.confirm(entry.memory_id)
    assert store.read_document(entry.memory_id) == draft
    assert item(store.sync_documents(), entry.memory_id)["status"] == "stale"
