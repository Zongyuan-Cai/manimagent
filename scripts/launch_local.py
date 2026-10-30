"""Start the local project and open its browser once the service is ready."""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from manimagent.api import create_app


def main():
    os.chdir(Path(__file__).resolve().parents[1])
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
        raise SystemExit(
            "Ports 8000-8010 are busy; close an old server or use manimagent serve --port."
        )
    url = f"http://127.0.0.1:{port}"

    def open_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url + "/api/v2/health", timeout=1):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.5)

    threading.Thread(target=open_ready, daemon=True).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
