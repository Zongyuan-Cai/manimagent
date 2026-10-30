"""Package an allowlisted source snapshot and a tested example, without runtime secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--example", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    marker = destination / "delivery.json"
    if destination.exists() and any(destination.iterdir()) and not marker.exists():
        raise SystemExit(
            "Destination contains files and is not a previous delivery; choose a new folder."
        )
    destination.mkdir(parents=True, exist_ok=True)
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    )
    paths = sorted(set(p for p in raw.decode("utf-8").split("\0") if p))
    copied = []
    for name in paths:
        relative = Path(name)
        if (
            relative.parts[0] in {"runs", "dist", "build", ".git", ".venv"}
            or relative.name == "config.yaml"
            or (relative.name.startswith(".env") and ".example" not in relative.name)
        ):
            continue
        source = (ROOT / relative).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative.as_posix())
    examples = destination / "examples" / "vector-projection"
    examples.mkdir(parents=True, exist_ok=True)
    result = json.loads((args.example / "result.json").read_text(encoding="utf-8"))
    if not result.get("passed"):
        raise SystemExit("Example has not passed real end-to-end validation")
    for name in ["example.mp4", "answer.md", "plan.json", "video.json", "result.json"]:
        shutil.copyfile(args.example / name, examples / name)
        copied.append((examples / name).relative_to(destination).as_posix())
    for file in (ROOT / "dist").glob("manimagent-0.3.0*"):
        target = destination / "dist" / file.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(file, target)
        copied.append(target.relative_to(destination).as_posix())
    manifest = {
        "version": "0.3.0",
        "example": result,
        "files": {
            name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in copied
        },
    }
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    copied.append("delivery.json")
    archive = destination.with_name(destination.name + "-source.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name in copied:
            bundle.write(destination / name, arcname=destination.name + "/" + name)
    sample = destination.parent / "ManimAgent-示例-向量投影.mp4"
    shutil.copyfile(examples / "example.mp4", sample)
    print(
        json.dumps(
            {
                "project": str(destination),
                "archive": str(archive),
                "example_video": str(sample),
                "files": len(copied),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
