"""Start the local project and open its browser once the service is ready."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from manimagent.api import create_app
from manimagent.errors import ContentError


def existing_service(root):
    """Only reopen the service that owns this exact data directory."""
    try:
        owner = json.loads((root / "instance.json").read_text(encoding="utf-8"))
        url = owner.get("url") if isinstance(owner, dict) else None
        if not isinstance(url, str):
            return None
        address = urlsplit(url)
        if (
            address.scheme != "http"
            or address.hostname != "127.0.0.1"
            or not address.port
            or address.username
            or address.password
            or address.path
            or address.query
            or address.fragment
        ):
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/v2/health", timeout=1) as response:
            if json.load(response).get("instance_id") == owner["id"]:
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def main():
    os.chdir(Path(__file__).resolve().parents[1])
    app = create_app()
    manager = app.state.job_manager
    try:
        manager.start()
    except ContentError as exc:
        if exc.error.code != "instance_busy":
            raise
        for _ in range(10):
            if url := existing_service(manager.store.root):
                webbrowser.open(url)
                return
            time.sleep(0.3)
        raise SystemExit(str(exc)) from exc

    def open_ready():
        for _ in range(60):
            if service := existing_service(manager.store.root):
                webbrowser.open(service)
                return
            time.sleep(0.5)

    try:
        # Keep the chosen port bound until Uvicorn takes ownership; probing then
        # closing it lets another application steal the advertised port.
        with socket.socket() as sock:
            for port in range(8000, 8011):
                try:
                    sock.bind(("127.0.0.1", port))
                    break
                except OSError:
                    continue
            else:
                raise SystemExit(
                    "Ports 8000-8010 are busy; close an old server or use manimagent serve --port."
                )
            manager.instance.publish(f"http://127.0.0.1:{port}")
            threading.Thread(target=open_ready, daemon=True).start()
            uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, workers=1)).run(
                sockets=[sock]
            )
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
