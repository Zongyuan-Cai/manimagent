"""Record one distribution build and its credential-free installed smoke checks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
DIST = ROOT / "dist/memory-cache-20261106"
UV = Path("C:/Users/20557/.codex/worktrees/docs-av-alignment/manimagent/.venv/Scripts/uv.exe")
REQUIRED_MODULES = ["llm/context.py", "llm/usage.py", "memory/projection.py"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MANIMAGENT_", "MIMO_", "OPENAI_", "DEEPSEEK_", "ANTHROPIC_", "GEMINI_"))
    }
    env["PYTHONUTF8"] = "1"
    env["UV_NO_PROGRESS"] = "1"
    results = []

    def run(arguments: list[str], name: str, timeout: int = 300) -> None:
        started = time.monotonic()
        with (EVIDENCE / f"{name}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(arguments, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        results.append({"command": arguments, "exit_code": result.returncode, "seconds": round(time.monotonic() - started, 3), "log": f"{name}.log"})
        (EVIDENCE / "package-commands.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name}: exit {result.returncode}", flush=True)
        result.check_returncode()

    if DIST.exists() and any(DIST.glob("*.whl")):
        raise RuntimeError("Independent output already contains a wheel; refusing to overwrite this verification run")
    source_files = {
        path.relative_to(ROOT / "src").as_posix(): digest(path.read_bytes())
        for path in sorted((ROOT / "src/manimagent").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    snapshot = {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "python": sys.version, "runtime_files": source_files}
    (EVIDENCE / "package-source-manifest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    run([str(UV), "--version"], "package-uv-version")
    run([str(UV), "lock", "--check"], "package-lock")
    run([str(UV), "build", "--out-dir", str(DIST)], "package-build")

    packages = []
    for package in sorted(DIST.iterdir()):
        if package.suffix == ".whl":
            with zipfile.ZipFile(package) as archive:
                contents = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        elif package.name.endswith(".tar.gz"):
            with tarfile.open(package) as archive:
                contents = {"/".join(member.name.split("/")[2:]): archive.extractfile(member).read() for member in archive.getmembers() if member.isfile() and member.name.split("/")[1] == "src"}
        else:
            continue
        for name, expected_hash in source_files.items():
            if name not in contents or digest(contents[name]) != expected_hash:
                raise RuntimeError(f"Archive missing or changed runtime source: {package.name}: {name}")
        for relative in REQUIRED_MODULES:
            if f"manimagent/{relative}" not in contents:
                raise RuntimeError(f"Missing new module {relative}")
        packages.append({"filename": package.name, "bytes": package.stat().st_size, "sha256": digest(package.read_bytes()), "runtime_files_compared": len(source_files), "new_modules": REQUIRED_MODULES, "status": "runtime_bytes_match"})
    for name, expected_hash in source_files.items():
        if digest((ROOT / "src" / name).read_bytes()) != expected_hash:
            raise RuntimeError(f"Source changed during build: {name}")
    (EVIDENCE / "package-archives.json").write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"archive manifests: {len(packages)} packages; {len(source_files)} runtime files match", flush=True)
    run([sys.executable, "scripts/check_distribution.py", str(DIST), "--uv", str(UV)], "package-install-smoke", timeout=540)
    print("PASS: all archives and installed application checks", flush=True)


if __name__ == "__main__":
    main()
