"""Reproduce and fix the harness response-wrapping error without network access."""

import gzip
import hashlib
import json
from pathlib import Path

import httpx

root = Path(__file__).resolve().parent
payload = {"choices": [{"finish_reason": "stop", "message": {"content": "synthetic response"}}]}
wire = gzip.compress(json.dumps(payload).encode())
response = httpx.Response(200, headers={"content-encoding": "gzip", "content-length": str(len(wire))}, content=wire)
decoded = response.content
try:
    httpx.Response(200, headers=response.headers, content=decoded)
except httpx.DecodingError:
    reproduced = True
else:
    raise AssertionError("Expected the original double-decoding failure")
headers = [(name, value) for name, value in response.headers.multi_items()
    if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
rebuilt = httpx.Response(200, headers=headers, content=decoded)
assert rebuilt.json() == payload
manifest = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
original_hash = hashlib.sha256((root / "run-as-executed.py").read_bytes()).hexdigest()
assert manifest["script_sha256"] == original_hash
result = {"real_network_calls": 0, "double_decoding_reproduced": reproduced,
    "corrected_response_parses": rebuilt.json() == payload,
    "as_executed_script_sha256": original_hash,
    "corrected_script_sha256": hashlib.sha256((root / "run.py").read_bytes()).hexdigest(),
    "corrected_script_not_executed_against_provider": True}
(root / "offline-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
