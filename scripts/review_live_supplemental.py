"""Real-provider configuration, file-boundary, reuse and persistence review."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import ModelConfig, Settings
from manimagent.llm_settings import dpapi
from scripts.review_live_text import KEY_ENV, ROOT, save


def wait(client, response):
    assert response.status_code == 202, f"submit HTTP {response.status_code}"
    identifier = response.json()["job_id"]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        r = client.get(f"/api/v2/jobs/{identifier}")
        assert r.status_code == 200, f"poll HTTP {r.status_code}"
        job = r.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.15)
    raise TimeoutError("job timed out")


def submit(client, inputs=None, instruction="完整解答材料中的题目并引用来源。", source=None):
    return client.post(
        "/api/v2/generations",
        json={
            "source": source
            or {
                "type": "inputs",
                "inputs": inputs or [{"type": "text", "text": "17+25=?"}],
                "instruction": instruction,
            }
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--secret-file", required=True, type=Path)
    args = parser.parse_args()
    secret = dpapi(args.secret_file.read_bytes(), decrypt=True).decode()
    os.environ[KEY_ENV] = secret
    results = []

    def record(name, fn):
        started = time.monotonic()
        result = {"name": name, "passed": False}
        try:
            result.update(fn())
            result["passed"] = True
        except Exception as exc:
            result["failure"] = type(exc).__name__ + ": " + str(exc)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        save(args.output / name / "result.json", result)
        save(
            args.output / "summary.json",
            {"cases": results, "passed": sum(x["passed"] for x in results), "total": len(results)},
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)

    cfg = ModelConfig(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key_env=KEY_ENV,
        context_window=64000,
    )
    settings = Settings(
        runs_dir=args.output / "runtime", models={"text": cfg}, answer_context_tokens=48000
    )
    variants = [
        ("root", {}),
        ("v1", {"base_url": "https://api.deepseek.com/v1"}),
        ("full_url", {"base_url": "", "generate_url": "https://api.deepseek.com/chat/completions"}),
        ("prompt_json", {"json_mode": False}),
        ("completion_token_parameter", {"token_parameter": "max_completion_tokens"}),
        ("custom_auth_header", {"auth_mode": "api_key_header", "api_key_header": "Authorization"}),
    ]
    with TestClient(create_app(settings)) as client:
        for name, patch in variants:
            for scope in ("text", "structured"):

                def probe(patch=patch, scope=scope):
                    model = cfg.model_copy(update=patch)
                    key = "Bearer " + secret if model.auth_mode == "api_key_header" else secret
                    r = client.post(
                        "/api/v2/llm-settings/test",
                        json={
                            "model_id": "text",
                            "scope": scope,
                            "draft": {"config": model.model_dump(), "api_key": key},
                        },
                    )
                    data = r.json()
                    assert r.status_code == 200, (
                        f"HTTP {r.status_code}: {data.get('error', {}).get('code')}"
                    )
                    assert data["ok"]
                    assert secret not in r.text
                    return {"usage": data["usage"], "status": r.status_code}

                record(f"probe_{name}_{scope}", probe)

        def auth_failure():
            r = client.post(
                "/api/v2/llm-settings/test",
                json={
                    "model_id": "text",
                    "scope": "text",
                    "draft": {"config": cfg.model_dump(), "api_key": "invalid-review-credential"},
                },
            )
            assert r.status_code == 502 and r.json()["error"]["code"] == "provider_auth_error"
            return {"status": r.status_code, "provider_http_calls": 1}

        record("invalid_key", auth_failure)

        file_cases = [
            ("chinese_pdf", "中文题目.pdf", "completed", None),
            ("pdf_page_2", "分页材料.pdf", "completed", {"kind": "pages", "start": 2, "end": 2}),
            (
                "pdf_invalid_page",
                "分页材料.pdf",
                "needs_input",
                {"kind": "pages", "start": 99, "end": 100},
            ),
            ("blank_page", "空白页.pdf", "needs_review", None),
            ("scanned", "扫描页.pdf", "needs_review", None),
            ("diagram", "题图.pdf", "needs_review", None),
            ("docx_equation", "公式对象.docx", "needs_review", None),
            ("docx_image", "内嵌图片.docx", "needs_review", None),
            ("encrypted", "加密.pdf", "needs_input", None),
            ("corrupt", "损坏.pdf", "needs_input", None),
            ("empty", "空文件.txt", "needs_input", None),
        ]
        for name, filename, expected, selection in file_cases:

            def file_case(filename=filename, expected=expected, selection=selection, name=name):
                content = (ROOT / "testmaterial" / filename).read_bytes()
                r = client.post("/api/v2/files", files={"file": (filename, content)})
                assert r.status_code == 201
                entry = {"type": "file", "file_id": r.json()["file_id"]}
                if selection:
                    entry["selection"] = selection
                job = wait(client, submit(client, [entry]))
                save(args.output / name / "job.json", job)
                assert job["status"] == expected, f"expected {expected}, got {job['status']}"
                gen = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
                save(args.output / name / "generation.json", gen)
                if expected != "completed":
                    assert job["usage"]["model_calls"] == 0
                    assert gen["answer_id"] is None
                else:
                    answer = client.get(f"/api/v2/answers/{gen['answer_id']}").json()
                    save(args.output / name / "answer.md", answer["markdown"])
                    if selection:
                        material = client.get(f"/api/v2/materials/{gen['material_ids'][0]}").json()
                        assert all(b["source"]["locator"]["page"] == 2 for b in material["blocks"])
                return {"status": job["status"], "usage": job["usage"]}

            record(name, file_case)

        state = {}

        def initial_answer():
            job = wait(client, submit(client))
            assert job["status"] == "completed"
            gen = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
            answer = client.get(f"/api/v2/answers/{gen['answer_id']}").json()
            assert "42" in answer["markdown"]
            state.update(answer=answer, material_ids=gen["material_ids"])
            return {"usage": job["usage"]}

        record("reuse_seed", initial_answer)

        def revision_and_reuse():
            original = state["answer"]
            r = client.post(
                "/api/v2/answers/revisions",
                json={
                    "base_answer_id": original["answer_id"],
                    "markdown": original["markdown"] + "\n\n人工补充：核算 17+25=42。",
                },
            )
            assert r.status_code == 201
            revised = r.json()
            assert revised["derived_from_answer_id"] == original["answer_id"]
            job = wait(
                client, submit(client, source={"type": "answer", "answer_id": revised["answer_id"]})
            )
            assert job["status"] == "completed" and job["usage"]["model_calls"] == 0
            assert client.get(f"/api/v2/answers/{original['answer_id']}").json() == original
            state["revised"] = revised
            return {"usage": job["usage"], "version_preserved": True}

        record("revision_zero_call_reuse", revision_and_reuse)

        def material_reuse():
            job = wait(
                client,
                submit(
                    client,
                    source={
                        "type": "materials",
                        "material_ids": state["material_ids"],
                        "instruction": "根据原材料的两个数17和25，计算17减25，说明结果。",
                    },
                ),
            )
            assert job["status"] == "completed"
            gen = client.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
            answer = client.get(f"/api/v2/answers/{gen['answer_id']}").json()
            assert re_search_negative_eight(answer["markdown"])
            return {"usage": job["usage"]}

        record("material_reuse_new_question", material_reuse)

        def configure_roles():
            view = client.get("/api/v2/llm-settings").json()
            models = {
                "writer_live": {"config": cfg.model_dump(), "api_key": secret},
                "checker_live": {
                    "config": cfg.model_copy(
                        update={"base_url": "https://api.deepseek.com/v1", "json_mode": False}
                    ).model_dump(),
                    "api_key": secret,
                },
            }
            r = client.put(
                "/api/v2/llm-settings",
                json={
                    "expected_revision": view["revision"],
                    "models": models,
                    "roles": {"answer_writer": "writer_live", "answer_checker": "checker_live"},
                },
            )
            assert r.status_code == 200 and secret not in r.text
            job = wait(client, submit(client))
            assert job["status"] == "completed"
            record_file = client.app.state.text_manager.store.load_record(job["job_id"])
            assert record_file["settings"]["roles"] == {
                "answer_writer": "writer_live",
                "answer_checker": "checker_live",
            }
            assert [c["role"] for c in record_file["calls"] if c.get("event") == "request"] == [
                "answer_writer",
                "answer_checker",
            ]
            assert secret not in json.dumps(record_file)
            return {"usage": job["usage"], "distinct_bindings": True, "vendor_count": 1}

        record("ui_save_two_role_bindings", configure_roles)

    with TestClient(create_app(settings)) as restarted:

        def restart():
            assert restarted.get("/api/v2/health").json()["ready"]
            assert (
                restarted.get("/api/v2/llm-settings").json()["roles"]["answer_writer"]
                == "writer_live"
            )
            assert (
                restarted.get(f"/api/v2/answers/{state['revised']['answer_id']}").status_code == 200
            )
            job = wait(restarted, submit(restarted))
            assert job["status"] == "completed"
            return {"usage": job["usage"], "configuration_and_answers_persisted": True}

        record("restart_configuration_and_generate", restart)

    for name, output_limit, calls, expected, code in [
        ("truncation", 256, 4, "needs_review", "output_truncated"),
        ("call_budget", 6000, 1, "failed", "budget_exceeded"),
    ]:

        def boundary(
            name=name, output_limit=output_limit, calls=calls, expected=expected, code=code
        ):
            cfg2 = cfg.model_copy(update={"max_output_tokens": output_limit})
            s = Settings(
                runs_dir=args.output / name / "runtime",
                models={"text": cfg2},
                max_model_calls=calls,
                answer_context_tokens=48000,
            )
            with TestClient(create_app(s)) as c:
                instruction = (
                    "写出20种求和方法，每种都展开详细推导，引用原材料。"
                    if name == "truncation"
                    else "计算17+25并引用。"
                )
                job = wait(c, submit(c, instruction=instruction))
                save(args.output / name / "job.json", job)
                assert job["status"] == expected and job["error"]["code"] == code, str(job["error"])
                gen = c.get(f"/api/v2/generations/{job['resource_ref']['id']}").json()
                assert gen["answer_id"] is None
                return {"status": job["status"], "usage": job["usage"]}

        record(name, boundary)


def re_search_negative_eight(markdown):
    import re

    return re.search(r"[-−]\s*8", markdown) is not None


if __name__ == "__main__":
    main()
