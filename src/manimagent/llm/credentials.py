"""Local credential protection and reference resolution; values never enter Settings."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import uuid
from pathlib import Path

from manimagent.content_store import read_json_bytes, write_json
from manimagent.errors import ContentError


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    """Use the Windows user's credential protection; no external crypto dependency."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ContentError("credential_unavailable", "无法读取本地密钥，请重新输入", 503)
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class CredentialStore:
    def __init__(self, path: Path, lock: threading.RLock):
        self.path = path
        self.lock = lock

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(read_json_bytes(self.path))

    def resolve(self, reference: str) -> str | None:
        if not reference.startswith("local_secret_"):
            return os.getenv(reference)
        with self.lock:
            entry = self._read().get(reference)
        if entry is None:
            return None
        if entry["encoding"] == "windows_dpapi":
            if os.name != "nt":
                raise ContentError(
                    "credential_unavailable", "此密钥绑定原 Windows 用户，请重新输入", 503
                )
            return dpapi(base64.b64decode(entry["value"]), decrypt=True).decode("utf-8")
        return entry["value"]

    def add(self, keys: dict[str, str]) -> dict[str, str]:
        """Store new secrets first; retain older references for accepted job snapshots."""
        with self.lock:
            saved = self._read()
            references = {}
            for identifier, raw in keys.items():
                reference = "local_secret_" + uuid.uuid4().hex
                saved[reference] = {
                    "encoding": "windows_dpapi" if os.name == "nt" else "plain",
                    "value": base64.b64encode(dpapi(raw.encode())).decode()
                    if os.name == "nt"
                    else raw,
                }
                references[identifier] = reference
            write_json(self.path, saved)
            return references
