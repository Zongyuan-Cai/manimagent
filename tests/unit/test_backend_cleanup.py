from __future__ import annotations

import json
import socket
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentError, ContentStore, write_json
from manimagent.materials import MaterialService
from manimagent.schemas import InputsSource, ParseRequest, Usage
from scripts import launch_local, package_desktop, review_live_cli, review_live_text
from scripts.review_support import wait_job


@pytest.mark.parametrize("kind", ["upload", "diagnostic", "generated"])
def test_all_asset_publishers_roll_back_if_metadata_cannot_be_saved(tmp_path, monkeypatch, kind):
    store = ContentStore(Settings(private_dir=tmp_path / "private"))
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
    store = ContentStore(Settings(private_dir=tmp_path / "private", max_upload_bytes=1))
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


def test_generation_source_reuses_parse_contract_without_expanding_parser_audit(tmp_path):
    settings = Settings(private_dir=tmp_path)
    source = InputsSource(
        type="inputs", inputs=[{"type": "text", "text": "17+25"}], instruction="求和"
    )
    record = {}
    result = MaterialService(ContentStore(settings)).parse(
        source, Budget(settings, Usage()), record
    )
    assert result.blocks[0].content == "17+25"
    assert set(record["parse"]["request"]) == set(ParseRequest.model_fields)


def delivery_fixture(tmp_path, monkeypatch):
    root, destination, example = (tmp_path / name for name in ("source", "delivery", "example"))
    root.mkdir()
    example.mkdir()
    for name in package_desktop.EXAMPLE_FILES:
        (example / name).write_bytes(b'{"passed": true}' if name == "result.json" else b"example")
    monkeypatch.setattr(package_desktop, "ROOT", root)
    monkeypatch.setattr(
        sys,
        "argv",
        ["package_desktop", "--destination", str(destination), "--example", str(example)],
    )
    return root, destination, example


def test_delivery_refresh_removes_only_previous_manifest_files_and_excludes_secrets(
    tmp_path, monkeypatch
):
    root, destination, _ = delivery_fixture(tmp_path, monkeypatch)
    for name in ("README.md", "src/manimagent/obsolete.py", "docs/credentials.json", "scratch.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    names = ["README.md", "src/manimagent/obsolete.py", "docs/credentials.json", "scratch.txt"]
    monkeypatch.setattr(
        package_desktop.subprocess, "check_output", lambda *a, **k: "\0".join(names).encode()
    )
    package_desktop.main()
    assert (destination / "src/manimagent/obsolete.py").is_file()
    assert not (destination / "docs/credentials.json").exists()
    assert not (destination / "scratch.txt").exists()
    (destination / "user-note.txt").write_text("keep", encoding="utf-8")
    names.remove("src/manimagent/obsolete.py")
    package_desktop.main()
    assert not (destination / "src/manimagent/obsolete.py").exists()
    assert (destination / "user-note.txt").read_text() == "keep"
    with zipfile.ZipFile(destination.with_name("delivery-source.zip")) as archive:
        assert "delivery/src/manimagent/obsolete.py" not in archive.namelist()
        assert "delivery/user-note.txt" not in archive.namelist()


def test_delivery_rejects_manifest_path_escape_before_touching_files(tmp_path, monkeypatch):
    _, destination, _ = delivery_fixture(tmp_path, monkeypatch)
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (destination / "delivery.json").write_text(
        json.dumps({"files": {"../outside.txt": "old digest"}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="unsafe path"):
        package_desktop.main()
    assert outside.read_text() == "keep"


def test_delivery_checks_example_before_creating_destination(tmp_path, monkeypatch):
    _, destination, example = delivery_fixture(tmp_path, monkeypatch)
    (example / "video.json").unlink()
    with pytest.raises(SystemExit, match="every artifact"):
        package_desktop.main()
    assert not destination.exists()


@pytest.mark.parametrize("passed", [False, True])
def test_live_text_review_exit_code_reflects_saved_case_results(tmp_path, monkeypatch, passed):
    monkeypatch.setenv(review_live_text.KEY_ENV, "unused-test-key")
    monkeypatch.setattr(sys, "argv", ["review", "--output", str(tmp_path)])
    monkeypatch.setattr(review_live_text, "cases", lambda: [{"name": "case"}])
    monkeypatch.setattr(review_live_text, "run_case", lambda *a: {"name": "case", "passed": passed})
    assert review_live_text.main() == int(not passed)
    assert json.loads((tmp_path / "summary.json").read_bytes())["passed"] == int(passed)


def test_cli_review_rejects_success_text_without_a_persisted_job(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_bytes(b"dummy")
    monkeypatch.setattr(
        sys, "argv", ["review", "--output", str(tmp_path), "--secret-file", str(secret)]
    )
    monkeypatch.setattr(review_live_cli, "dpapi", lambda *a, **k: b"unused-test-key")
    monkeypatch.setattr(review_live_cli, "pdf_bytes", lambda *a: b"fixture")

    def fake_run(command, **kwargs):
        if command[-1] == "doctor":
            return subprocess.CompletedProcess(command, 0, '{"ready": true}', "")
        return subprocess.CompletedProcess(command, 0, "42 [[material_fake:block_fake]]", "")

    monkeypatch.setattr(review_live_cli.subprocess, "run", fake_run)
    assert review_live_cli.main() == 1
    result = json.loads((tmp_path / "text/result.json").read_bytes())
    assert not result["passed"] and "CLI job count differs" in result["failure"]


def test_shared_live_polling_reports_http_errors_before_reading_job_shape():
    class Response:
        status_code = 503

    class Client:
        def get(self, path):
            return Response()

    with pytest.raises(AssertionError, match="poll HTTP 503"):
        wait_job(Client(), "job_test", timeout=1)


@pytest.mark.parametrize("publish_fails", [False, True])
def test_launcher_retains_port_reservation_and_releases_worker_on_startup_failure(
    tmp_path, monkeypatch, publish_fails
):
    stopped, served = [], []

    def publish(url):
        if publish_fails:
            raise OSError("cannot publish owner")

    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: stopped.append(True),
        instance=SimpleNamespace(publish=publish),
        store=SimpleNamespace(root=tmp_path),
    )
    monkeypatch.setattr(
        launch_local,
        "create_app",
        lambda: SimpleNamespace(state=SimpleNamespace(text_manager=manager)),
    )
    monkeypatch.setattr(
        launch_local.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None)
    )

    class Server:
        def __init__(self, config):
            pass

        def run(self, sockets):
            address = sockets[0].getsockname()
            with socket.socket() as other:
                with pytest.raises(OSError):
                    other.bind(address)
            served.append(True)

    monkeypatch.setattr(launch_local.uvicorn, "Server", Server)
    if publish_fails:
        with pytest.raises(OSError, match="cannot publish owner"):
            launch_local.main()
    else:
        launch_local.main()
    assert stopped == [True]
    assert served == ([] if publish_fails else [True])


@pytest.mark.parametrize("owner", [[], {"url": 42}, {"url": "http://127.0.0.1:8000@remote.test"}])
def test_launcher_rejects_malformed_or_nonlocal_owners_without_http(tmp_path, monkeypatch, owner):
    (tmp_path / "instance.json").write_text(json.dumps(owner), encoding="utf-8")

    def unexpected_request(*args, **kwargs):
        raise AssertionError("unexpected HTTP request")

    monkeypatch.setattr(launch_local.urllib.request, "build_opener", unexpected_request)
    assert launch_local.existing_service(tmp_path) is None


@pytest.mark.parametrize(
    "script",
    ["review_live_text", "review_live_supplemental", "review_live_cli", "test_deepseek_e2e"],
)
def test_live_review_scripts_support_direct_and_module_entrypoints(script):
    root = Path(__file__).resolve().parents[2]
    for arguments in ([str(root / "scripts" / f"{script}.py")], ["-m", f"scripts.{script}"]):
        result = subprocess.run(
            [sys.executable, *arguments, "--help"], cwd=root, capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr.decode("utf-8")
