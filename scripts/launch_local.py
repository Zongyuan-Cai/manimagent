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

import uvicorn

from manimagent.api import create_app
from manimagent.content_store import ContentError


def existing_service(root):
    """Only reopen the service that owns this exact data directory."""
    try:
        owner = json.loads((root / "instance.json").read_text(encoding="utf-8"))
        url = owner.get("url")
        if not url or not url.startswith("http://127.0.0.1:"):
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
    manager = app.state.text_manager
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
    port = None
    for candidate in range(8000, 8011):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                port = candidate
                break
            except OSError:
                continue
    if port is None:
        manager.stop()
        raise SystemExit(
            "Ports 8000-8010 are busy; close an old server or use manimagent serve --port."
        )
    url = f"http://127.0.0.1:{port}"
    manager.instance.publish(url)

    def open_ready():
        for _ in range(60):
            if service := existing_service(manager.store.root):
                webbrowser.open(service)
                return
            time.sleep(0.5)

    threading.Thread(target=open_ready, daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, workers=1)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
