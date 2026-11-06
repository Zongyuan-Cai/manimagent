from __future__ import annotations

import json
from io import BytesIO

import pytest

from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.storage import write_json
from tests.support import store_for


@pytest.mark.parametrize("kind", ["upload", "diagnostic", "generated"])
def test_all_asset_publishers_roll_back_if_metadata_cannot_be_saved(tmp_path, monkeypatch, kind):
    store = store_for(Settings(private_dir=tmp_path / "private"))
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"rendered video")

    def fail_save(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(store, "save", fail_save)
    with pytest.raises(OSError, match="storage unavailable"):
        if kind == "upload":
            store.put_file(BytesIO(b"original"), "material.txt")
        elif kind == "diagnostic":
            store.diagnostic({"candidate": "retained only on successful publication"})
        else:
            store.generated_file(rendered, "teaching.mp4", "video/mp4")
    assert not list((store.root / "files").iterdir())
    assert rendered.read_bytes() == b"rendered video"


def test_internal_assets_keep_integrity_without_inheriting_upload_limits(tmp_path):
    store = store_for(Settings(private_dir=tmp_path / "private", max_upload_bytes=1))
    with pytest.raises(ContentError) as oversized:
        store.put_file(BytesIO(b"too large"), "material.txt")
    assert oversized.value.error.code == "upload_too_large"
    diagnostic = store.diagnostic({"message": "中文候选内容"})
    asset = store.verify_file(diagnostic)
    assert asset.media_type == "application/json"
    assert json.loads(store.file_path(diagnostic).read_bytes()) == {"message": "中文候选内容"}
    rendered = tmp_path / "rendered.mp4"
    rendered.write_bytes(b"trusted renderer output")
    video = store.generated_file(rendered, "teaching.mp4", "video/mp4")
    assert store.verify_file(video.file_id) == video
    assert store.file_path(video.file_id).read_bytes() == rendered.read_bytes()


def test_json_serialization_failure_preserves_previous_snapshot_and_cleans_temporary(tmp_path):
    path = tmp_path / "private.json"
    write_json(path, {"revision": 1})
    with pytest.raises(TypeError):
        write_json(path, {"revision": object()})
    assert json.loads(path.read_bytes()) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]
