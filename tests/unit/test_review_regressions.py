import io

import httpx
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from manimagent.answers import derive_answer
from manimagent.api import create_app
from manimagent.config import Settings
from manimagent.markdown_view import render_markdown
from manimagent.providers import ChatProvider
from manimagent.schemas import Generation, Job, TextGenerationRequest
from manimagent.text_jobs import TextJobManager
from tests.integration.test_pipeline import controlled_provider, pdf_bytes, settings_for, submit
from tests.support import wait_job


def test_pdf_content_stream_without_dictionary_entries_still_detects_vector_graphics(tmp_path):
    reader = PdfReader(io.BytesIO(pdf_bytes(["Triangle diagram is essential."])))
    writer = PdfWriter()
    writer.append(reader)
    content = DecodedStreamObject()
    content.set_data(
        writer.pages[0].get_contents().get_data() + b"\n100 100 m 200 100 l 150 200 l h S\n"
    )
    writer.pages[0][NameObject("/Contents")] = writer._add_object(content)
    data = io.BytesIO()
    writer.write(data)
    assert not bool(writer.pages[0].get_contents())
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        asset = client.post(
            "/api/v2/files", files={"file": ("diagram.pdf", data.getvalue())}
        ).json()
        request = client.post(
            "/api/v2/materials/parse",
            json={"inputs": [{"type": "file", "file_id": asset["file_id"]}]},
        )
        job = wait_job(client, request.json()["job_id"])
        assert job["status"] == "needs_review" and job["usage"]["model_calls"] == 0


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


def test_empty_file_requests_input_without_spending_model_calls(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(
        create_app(
            settings,
            text_provider=ChatProvider(
                settings,
                httpx.MockTransport(
                    lambda _: (_ for _ in ()).throw(AssertionError("must not call"))
                ),
            ),
        )
    ) as client:
        asset = client.post("/api/v2/files", files={"file": ("empty.txt", b"")}).json()
        job = wait_job(
            client,
            submit(client, inputs=[{"type": "file", "file_id": asset["file_id"]}]).json()["job_id"],
        )
        assert job["status"] == "needs_input" and job["usage"]["model_calls"] == 0


def test_shutdown_updates_both_job_and_generation_stages(tmp_path):
    queued = TextJobManager(settings_for(tmp_path / "queued"))
    job = queued.submit(
        "generation",
        TextGenerationRequest(
            source={
                "type": "inputs",
                "inputs": [{"type": "text", "text": "a=3"}],
                "instruction": "q",
            }
        ),
    )
    queued.stop()
    result = queued.store.load("jobs", job.job_id, Job)
    generation = queued.store.load("generations", job.resource_ref.id, Generation)
    assert result.status == "interrupted" and result.error.code == "interrupted"
    assert all(s.status == "interrupted" for s in generation.stages.values())


def test_markdown_title_ignores_hash_characters_inside_fenced_code():
    material = "material_" + "a" * 32
    block = "block_" + "b" * 32
    markdown = (
        f"```python\n# this is a comment\n```\n\n## Actual title\nAnswer [[{material}:{block}]]"
    )
    answer = derive_answer(
        markdown,
        [material],
        "question",
        {"allowed_source_refs": [{"material_id": material, "block_id": block}]},
    )
    assert answer.title == "Actual title" and answer.markdown == markdown


def test_real_model_display_math_without_blank_lines_keeps_latex_delimiters():
    markdown = (
        "已知  \n\\[\na=(3,2),\\qquad b=(2,0).\n\\]\n计算结果如下。\n\n所以\n$$c=\\frac32$$\n结论。"
    )
    html = render_markdown(markdown)
    assert html.count('class="math"') == 2
    assert r"\[" in html and r"\frac32" in html
    assert "计算结果如下" in html and "结论" in html


def test_cjk_bold_punctuation_renders_without_changing_code_blocks():
    html = render_markdown("**来源引用说明：**题目来自材料。\n\n```\n**说明：**代码\n```")
    assert "<strong>来源引用说明：</strong>题目" in html
    assert "**说明：**代码" in html


def test_updated_application_assets_are_not_hidden_by_browser_conditional_cache(tmp_path):
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        initial = client.get("/static/app.js")
        response = client.get(
            "/static/app.js",
            headers={
                "If-None-Match": initial.headers["etag"],
                "If-Modified-Since": initial.headers["last-modified"],
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/").headers["cache-control"] == "no-store"
