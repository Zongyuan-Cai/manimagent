from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from manimagent.answers.context import ContextAssembler
from manimagent.answers.service import AnswerService
from manimagent.budget import Budget
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.personalization import EffectiveProfile
from manimagent.schemas import (
    Answer,
    AnswerCheck,
    AnswerDocument,
    AnswerRequest,
    AnswerRevisionRequest,
    MaterialIR,
    Usage,
)
from manimagent.teaching import artifact_digest, render_document
from manimagent.video.review import VideoReviewer
from manimagent.video.units import teaching_units
from tests.support.pipeline import settings_for


def setup(tmp_path, *, decision="pass", answer_check=True):
    settings = settings_for(tmp_path, answer_check=answer_check)
    store = ContentStore(settings.runs_dir, settings.max_upload_bytes)
    material_id, block_id = new_id("material"), new_id("block")
    material = MaterialIR(
        material_id=material_id,
        source_file_ids=[],
        blocks=[
            {
                "block_id": block_id,
                "content": "给定 x=1，计算 x+x",
                "source": {"input_index": 0},
                "review_state": "verified",
            }
        ],
    )
    store.save("materials", material_id, material)
    document = AnswerDocument.model_validate(
        {
            "title": "计算",
            "units": [
                {
                    "unit_id": "u001",
                    "title": "计算结果",
                    "content": "由 x=1 可得 x+x=2。",
                    "source_refs": [{"material_id": material_id, "block_id": block_id}],
                    "depends_on": [],
                    "required": True,
                }
            ],
        }
    )
    calls = []

    def complete(role, instruction, payload, schema, budget):
        calls.append((role, payload))
        return (
            document
            if schema is AnswerDocument
            else AnswerCheck(decision=decision, message="受控检查")
        )

    provider = SimpleNamespace(settings=settings, complete=complete)
    service = AnswerService(store, provider, settings)
    record = {
        "job_id": new_id("job"),
        "effective_profile": EffectiveProfile().model_dump(mode="json"),
        "request_intent": {"overrides": [], "exclude_memory": [], "issues": []},
    }
    budget = Budget(settings, Usage())
    request = AnswerRequest(material_ids=[material_id], instruction="计算并解释")
    return SimpleNamespace(
        settings=settings,
        store=store,
        material=material,
        document=document,
        calls=calls,
        provider=provider,
        service=service,
        record=record,
        budget=budget,
        request=request,
    )


def test_generated_markdown_and_video_units_share_one_document_and_bound_check(tmp_path):
    b = setup(tmp_path)
    answer = b.service.generate(b.request, b.budget, b.record)
    assert answer.document == b.document
    assert answer.markdown == render_document(answer.document)
    assert answer.steps[0].step_id == answer.document.units[0].unit_id
    assert answer.steps[0].content == answer.document.units[0].content
    assert answer.verification.document_digest == artifact_digest(answer.document)
    assert answer.verification.source_digest == artifact_digest(
        ContextAssembler(b.store, b.settings).build_bundle(answer.material_ids)
    )
    assert teaching_units(answer)[0].source_refs == answer.document.units[0].source_refs
    checker = b.calls[-1][1]
    assert checker["candidate_document"] == b.document.model_dump(mode="json")
    assert (
        not {"candidate_markdown", "user_memory", "effective_profile", "teaching_spec"}
        & checker.keys()
    )
    assert b.record["source_bundle"]["schema_version"] == "source-bundle-1"


def test_rejected_candidate_stays_diagnostic_and_has_no_published_answer_or_pass_receipt(tmp_path):
    b = setup(tmp_path, decision="needs_review")
    with pytest.raises(ContentError):
        b.service.generate(b.request, b.budget, b.record)
    assert not list((b.store.root / "answers").glob("*.json"))
    assert not list((b.store.root / "answer_checks").glob("*.json"))
    record = b.store.load_record(b.record["job_id"])
    assert record["answer_document"] == b.document.model_dump(mode="json")
    assert record["answer_check"]["decision"] == "needs_review"


def test_unknown_sources_and_forged_legacy_output_are_rejected_before_content_check(tmp_path):
    b = setup(tmp_path)
    bad = b.document.model_dump(mode="json")
    bad["units"][0]["source_refs"][0]["block_id"] = new_id("block")
    b.provider.complete = lambda *args: AnswerDocument.model_validate(bad)
    with pytest.raises(ContentError, match="未提供"):
        b.service.generate(b.request, b.budget, b.record)
    bad["units"][0]["source_refs"][0]["block_id"] = b.material.blocks[0].block_id
    bad["origin"] = "legacy_markdown"
    with pytest.raises(ContentError, match="结构化"):
        b.service.generate(b.request, b.budget, b.record)


def test_manual_revision_stays_legacy_and_is_rechecked_without_overwriting_history(tmp_path):
    b = setup(tmp_path)
    base = b.service.generate(b.request, b.budget, b.record)
    revised = b.service.revise(
        AnswerRevisionRequest(
            base_answer_id=base.answer_id, markdown=base.markdown.replace("x+x=2", "x+x=1+1=2")
        )
    )
    assert revised.document is None and revised.verification is None
    assert revised.effective_profile == base.effective_profile
    reviewer = VideoReviewer(b.store, b.provider, b.settings, b.provider.complete)
    record = {"job_id": new_id("job")}
    before = len(b.calls)
    reviewer.require_content_check(revised, b.budget, record)
    assert len(b.calls) == before + 1
    assert revised.document.origin == "legacy_markdown"
    units = teaching_units(revised)
    assert len(units) == 1 and units[0].text == revised.markdown
    persisted = b.store.load("answers", revised.answer_id, Answer)
    assert persisted.document is None and persisted.verification is None
    reviewer.require_content_check(persisted, b.budget, {"job_id": new_id("job")})
    assert len(b.calls) == before + 1
    assert persisted.document.origin == "legacy_markdown"


def test_receipt_depends_on_sources_and_policy_but_not_current_model_configuration(tmp_path):
    b = setup(tmp_path)
    answer = b.service.generate(b.request, b.budget, b.record)
    reviewer = VideoReviewer(b.store, b.provider, b.settings, b.provider.complete)
    b.settings.models["text"].model = "another-checker"
    before = len(b.calls)
    reused = {"job_id": new_id("job")}
    reviewer.require_content_check(answer, b.budget, reused)
    assert len(b.calls) == before and reused["video_content_check"]["reused"]
    b.material.blocks[0].content += "，并说明理由"
    b.store.save("materials", b.material.material_id, b.material)
    reviewer.require_content_check(answer, b.budget, {"job_id": new_id("job")})
    assert len(b.calls) == before + 1
    path = b.store.path("answer_checks", answer.answer_id)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["verification"]["policy_hash"] = "0" * 64
    b.store.save("answer_checks", answer.answer_id, receipt)
    reviewer.require_content_check(answer, b.budget, {"job_id": new_id("job")})
    assert len(b.calls) == before + 2


def test_disabled_checker_does_not_create_verified_facts(tmp_path):
    b = setup(tmp_path, answer_check=False)
    answer = b.service.generate(b.request, b.budget, b.record)
    assert answer.document is not None and answer.verification is None
    with pytest.raises(ContentError):
        teaching_units(answer)


def test_source_bundle_is_frozen_and_requires_exact_allowlist(tmp_path):
    b = setup(tmp_path)
    bundle = ContextAssembler(b.store, b.settings).build_bundle(b.request.material_ids)
    with pytest.raises(ValidationError):
        bundle.materials[0].blocks[0].content = "被替换的原题"
    value = bundle.model_dump(mode="json")
    value["allowed_source_refs"][0]["block_id"] = new_id("block")
    with pytest.raises(ValidationError):
        type(bundle).model_validate(value)
