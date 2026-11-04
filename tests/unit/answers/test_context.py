from fastapi.testclient import TestClient

from manimagent.api import create_app
from tests.support import wait_job
from tests.support.pipeline import controlled_provider, settings_for, submit


def test_context_keeps_same_name_files_separate_and_provides_actual_filenames(tmp_path):
    import json

    settings = settings_for(tmp_path)
    calls = []
    with TestClient(
        create_app(settings, text_provider=controlled_provider(settings, calls))
    ) as client:
        assets = [
            client.post("/api/v2/files", files={"file": ("same.txt", value)}).json()
            for value in [b"a=(3,2)", b"b=(2,0)"]
        ]
        job = wait_job(
            client,
            submit(
                client, inputs=[{"type": "file", "file_id": a["file_id"]} for a in assets]
            ).json()["job_id"],
        )
        assert job["status"] == "completed"
        context = json.loads(calls[0]["messages"][1]["content"])
        files = context["materials"][0]["source_files"]
        assert [f["filename"] for f in files] == ["same.txt", "same.txt"]
        assert len({f["file_id"] for f in files}) == 2
        assert not any(f["filename"].startswith("/") for f in files)
