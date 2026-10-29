"""Check built archives and boot each distribution in a clean, credential-free environment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REQUIRED_ASSETS = {
    "manimagent/web/index.html",
    "manimagent/web/app.js",
    "manimagent/web/app.css",
    "manimagent/web/llm-settings.js",
    "manimagent/web/vendor/tex-svg.js",
}
FORBIDDEN_PARTS = {
    "legacy_mvp",
    "testmaterial",
    "testresult",
    "runs",
    ".git",
    ".venv",
    ".venv-before-separation",
    ".env",
    "config.yaml",
    "credentials.json",
}


def check_members(names: list[str], *, sdist: bool) -> None:
    normalized = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError(f"Unsafe archive path: {name}")
        if FORBIDDEN_PARTS.intersection(path.parts) or any(
            part.startswith(".env.") and not part.endswith(".example") for part in path.parts
        ):
            raise ValueError(f"Private or historical content in distribution: {name}")
        relative = PurePosixPath(*path.parts[1:]) if sdist else path
        normalized.add(str(relative))
    required = {f"src/{path}" if sdist else path for path in REQUIRED_ASSETS}
    if missing := required - normalized:
        raise ValueError(f"Missing packaged web assets: {sorted(missing)}")


def smoke_install(package: Path, uv: str, version: str) -> None:
    # No project cwd, .env, user credentials, or existing virtual environment is reused.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MANIMAGENT_", "UV_", "PYTHON")) and key != "VIRTUAL_ENV"
    }
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="manimagent-package-") as folder:
        work = Path(folder)
        venv = work / ".venv"
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [uv, "venv", "--python", sys.executable, str(venv)],
            cwd=work,
            env=env,
            check=True,
            timeout=120,
        )
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), str(package)],
            cwd=work,
            env=env,
            check=True,
            timeout=180,
        )
        provenance = subprocess.check_output(
            [python, "-I", "-c", "import manimagent;print(manimagent.__file__)"],
            cwd=work,
            env=env,
            text=True,
            encoding="utf-8",
            timeout=15,
        ).strip()
        if not Path(provenance).resolve().is_relative_to(venv.resolve()):
            raise ValueError(
                f"Application was imported outside the clean environment: {provenance}"
            )
        subprocess.run(
            [python, "-I", "-m", "manimagent", "--help"],
            cwd=work,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=15,
        )
        (work / "config.yaml").write_text("models: {}\n", encoding="utf-8")
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def get(path: str) -> bytes:
            with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                return response.read()

        with (work / "server.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [python, "-I", "-m", "manimagent", "serve", "--port", str(port)],
                cwd=work,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        health = json.loads(get("/api/v2/health"))
                        break
                    except (urllib.error.URLError, TimeoutError) as exc:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            raise RuntimeError(
                                f"Installed application did not start:\n{log.read()}"
                            ) from exc
                        time.sleep(0.2)
                if health["output_mode"] != "text" or health["ready"]:
                    raise ValueError(
                        "Smoke application must run in text mode without configured keys"
                    )
                if json.loads(get("/openapi.json"))["info"]["version"] != version:
                    raise ValueError(
                        "Installed application version differs from the source project"
                    )
                if "LLM 配置" not in get("/").decode("utf-8"):
                    raise ValueError("Packaged UI did not render its configuration controls")
                for asset in REQUIRED_ASSETS - {"manimagent/web/index.html"}:
                    if not get(asset.replace("manimagent/web/", "/static/", 1)):
                        raise ValueError(f"Empty packaged asset: {asset}")
                print(f"PASS: {package.name} - isolated install, CLI, HTTP API, UI and assets")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--uv", default="uv", help="uv executable (defaults to PATH)")
    args = parser.parse_args()
    uv = shutil.which(args.uv)
    if uv is None:
        parser.error("uv executable was not found")
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    expected = {
        f"manimagent-{version}-py3-none-any.whl",
        f"manimagent-{version}.tar.gz",
    }
    packages = sorted(
        path.resolve()
        for path in args.directory.iterdir()
        if path.name.endswith((".whl", ".tar.gz"))
    )
    if {path.name for path in packages} != expected:
        raise ValueError(f"Expected exactly these current distributions: {sorted(expected)}")
    for package in packages:
        sdist = package.name.endswith(".tar.gz")
        if sdist:
            with tarfile.open(package) as archive:
                if any(member.issym() or member.islnk() for member in archive.getmembers()):
                    raise ValueError("Distribution must not contain symbolic or hard links")
                check_members(archive.getnames(), sdist=True)
        else:
            with zipfile.ZipFile(package) as archive:
                check_members(archive.namelist(), sdist=False)
        smoke_install(package, uv, version)


if __name__ == "__main__":
    main()
