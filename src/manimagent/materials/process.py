"""Bound one disposable parser process to the job's wall-clock and stop budget."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from manimagent.budget import Budget
from manimagent.errors import ContentError


def parse_isolated(task: dict, root: Path, timeout: float, budget: Budget) -> dict:
    with tempfile.TemporaryDirectory(prefix="parse-", dir=root) as directory:
        task_path = Path(directory) / "input.json"
        output = Path(directory) / "output.json"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        with (Path(directory) / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-m", "manimagent.materials.worker", str(task_path), str(output)],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            try:
                deadline = time.monotonic() + min(timeout, budget.remaining())
                while process.poll() is None:
                    budget.remaining(deadline)
                    time.sleep(0.02)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        if not output.exists():
            raise ContentError(
                "invalid_file", "文件解析失败，请检查原件是否损坏", state="needs_input"
            )
        candidate = json.loads(output.read_text(encoding="utf-8"))
        if "error" in candidate:
            raise ContentError(**candidate["error"])
        return candidate
