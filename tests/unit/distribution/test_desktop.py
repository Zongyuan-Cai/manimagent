from __future__ import annotations

import json
import socket
import sys
import zipfile
from types import SimpleNamespace

import pytest

from scripts import launch_local, package_desktop


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
        lambda: SimpleNamespace(state=SimpleNamespace(job_manager=manager)),
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
