from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from pydantic import ValidationError

from manimagent.animation_driver import matrix_text
from manimagent.animation_models import SceneOutline, VideoOutline
from manimagent.api import create_app
from manimagent.config import Settings
from manimagent.content_store import ContentError, new_id
from manimagent.instance import InstanceLease
from manimagent.schemas import Job
from manimagent.text_jobs import TextJobManager
from tests.integration.test_pipeline import controlled_provider, settings_for, submit
from tests.support import wait_job


def scene(identifier):
    return dict(
        scene_id=identifier, title="测试", objective="测试目标", unit_ids=["u1"], beat_count=3
    )


@pytest.mark.parametrize("identifier", ["CON", "aux", "Nul", "PRN", "com1", "LPT9"])
def test_scene_directory_rejects_windows_devices_everywhere(identifier):
    with pytest.raises(ValidationError):
        SceneOutline(**scene(identifier))


@pytest.mark.parametrize("ids", [["s1", "S1", "s3"], ["s1", "s1", "s3"]])
def test_outline_rejects_shared_scene_paths_before_rendering(ids):
    with pytest.raises(ValidationError):
        VideoOutline(
            status="ready",
            message="就绪",
            title="测试",
            audience="学生",
            difficulty="basic",
            difficulty_reason="基础",
            duration_reason="完整",
            scenes=[scene(i) for i in ids],
        )


@pytest.mark.parametrize("kind", ["superscript", "subscript"])
def test_docdefaults_math_blocks_real_parser_and_preserves_original(tmp_path, kind):
    document = Document()
    defaults = document.styles.element.find(qn("w:docDefaults"))
    properties = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    align = OxmlElement("w:vertAlign")
    align.set(qn("w:val"), kind)
    properties.append(align)
    paragraph = document.add_paragraph()
    for text, baseline in [("x", True), ("2", False), ("=4", True)]:
        run = paragraph.add_run(text)
        if baseline:
            align = OxmlElement("w:vertAlign")
            align.set(qn("w:val"), "baseline")
            run._r.get_or_add_rPr().append(align)
    stream = BytesIO()
    document.save(stream)
    cfg = settings_for(tmp_path)
    calls = []
    with TestClient(create_app(cfg, controlled_provider(cfg, calls))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("math.docx", stream.getvalue())}
        ).json()
        job = wait_job(
            client, submit(client, [{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"]
        )
        assert job["status"] == "needs_review" and not calls
        assert job["usage"]["model_calls"] == 0
        assert client.get(f"/api/v2/files/{asset['file_id']}/content").content == stream.getvalue()


@pytest.mark.parametrize("value", [1.0, 1.0000001, 1.0000002, -123456.789, 1e-12, 1000000.0])
def test_matrix_labels_roundtrip_without_changing_math(value):
    assert float(matrix_text(value)) == value


def test_second_process_cannot_recover_live_jobs_and_crash_releases_lock(tmp_path):
    # A real separate interpreter owns the OS lock. Killing it must release ownership.
    code = """
import json, sys
from pathlib import Path
from manimagent.instance import InstanceLease
lease = InstanceLease(Path(sys.argv[1]))
lease.acquire()
print(json.dumps({'locked': True}), flush=True)
sys.stdin.read()
"""
    cfg = Settings(runs_dir=tmp_path)
    manager = TextJobManager(cfg)
    job = Job(job_id=new_id("job"), kind="parse", status="running")
    manager.save_job(job)
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(manager.store.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert json.loads(process.stdout.readline())["locked"]
        with pytest.raises(ContentError, match="已有服务"):
            manager.start()
        assert manager.store.load("jobs", job.job_id, Job).status == "running"
    finally:
        process.kill()
        process.communicate(timeout=10)
    manager.start()
    try:
        assert manager.store.load("jobs", job.job_id, Job).status == "interrupted"
        manager.start()  # Launcher + ASGI lifespan is idempotent.
    finally:
        manager.stop()
    lease = InstanceLease(manager.store.root)
    lease.acquire()
    lease.release()


def test_desktop_second_launch_reuses_the_real_http_service(tmp_path):
    root = Path(__file__).resolve().parents[2]
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "runs_dir: " + (tmp_path / "runtime").as_posix() + "\nmodels: {}\n", encoding="utf-8"
    )
    # Suppress opening an interactive browser; service startup/HTTP/OS locking are real.
    script = "import runpy,webbrowser;webbrowser.open=lambda url: print('OPEN '+url,flush=True);runpy.run_path('scripts/launch_local.py',run_name='__main__')"
    env = {**os.environ, "MANIMAGENT_CONFIG": str(cfg), "PYTHONIOENCODING": "utf-8"}
    with (tmp_path / "launcher.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script], cwd=root, env=env, stdout=log, stderr=log
        )
        try:
            metadata = tmp_path / "runtime/private/instance.json"
            deadline = time.monotonic() + 15
            owner = None
            while time.monotonic() < deadline:
                try:
                    candidate = json.loads(metadata.read_text(encoding="utf-8"))
                    if candidate.get("url"):
                        with build_opener(ProxyHandler({})).open(
                            candidate["url"] + "/api/v2/health", timeout=1
                        ) as response:
                            if json.load(response)["instance_id"] == candidate["id"]:
                                owner = candidate
                                break
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            assert owner, "Launcher failed to expose its owned HTTP service"
            second = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            assert second.returncode == 0, second.stderr
            assert "OPEN " + owner["url"] in second.stdout
            assert json.loads(metadata.read_text(encoding="utf-8"))["id"] == owner["id"]
            assert process.poll() is None
        finally:
            process.terminate()
            process.wait(timeout=10)
