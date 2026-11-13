"""Bound answer receipts and semantic reviews share one run's repair allowance."""

from __future__ import annotations

import json
from dataclasses import dataclass

from manimagent.answers.context import ContextAssembler
from manimagent.answers.prompts import CHECK_INSTRUCTION
from manimagent.answers.service import checker_profile_hash, derive_answer
from manimagent.errors import ContentError
from manimagent.memory.projection import user_context
from manimagent.schemas import AnswerCheck, AnswerDocument, AnswerUnit, VerifiedAnswer
from manimagent.storage import read_json_bytes
from manimagent.teaching import artifact_digest, document_steps, render_document, validate_sources
from manimagent.video.models import PlanCheck, digest
from manimagent.video.prompts import PLAN_PROMPT


@dataclass
class RepairBudget:
    """One allowance for outline, timing, layout and semantic repairs together."""

    used: int = 0
    limit: int = 2

    def available(self, count: int = 1) -> bool:
        return self.used + count <= self.limit

    def consume(self, error: ContentError) -> None:
        if not self.available():
            raise error
        self.used += 1


class VideoReviewer:
    def __init__(self, store, provider, settings, call):
        self.store, self.provider, self.settings, self.call = store, provider, settings, call

    def require_content_check(self, answer, budget, record):
        policy = digest(CHECK_INSTRUCTION)
        profile = checker_profile_hash(self.provider)
        sources = ContextAssembler(self.store, self.settings).build_bundle(answer.material_ids)
        document = answer.document
        if document is None:
            # Preserve exact user Markdown as an explicit legacy artifact. No heuristic
            # segmentation can invent the dependencies of a manually revised answer.
            legacy = derive_answer(
                answer.markdown,
                answer.material_ids,
                answer.instruction,
                sources.model_dump(mode="json"),
            )
            document = AnswerDocument(
                origin="legacy_markdown",
                title=answer.title,
                units=(
                    AnswerUnit(
                        unit_id="legacy_document",
                        title=answer.title,
                        content=answer.markdown,
                        source_refs=tuple(legacy.steps[0].source_refs),
                        depends_on=(),
                        required=True,
                    ),
                ),
            )
        validate_sources(document, sources)
        if render_document(document) != answer.markdown:
            raise ContentError(
                "content_uncertain",
                "教学文档与保存的正文不一致，请重新生成或显式修订",
                state="needs_review",
            )
        document_hash, source_hash = artifact_digest(document), artifact_digest(sources)
        record["video_source_bundle"] = sources.model_dump(mode="json")
        receipt_path = self.store.path("answer_checks", answer.answer_id)
        if receipt_path.exists():
            saved = json.loads(read_json_bytes(receipt_path))
            if "verification" in saved:
                receipt = VerifiedAnswer.model_validate(saved["verification"])
                checked_document = AnswerDocument.model_validate(saved["document"])
                if (
                    receipt.answer_id == answer.answer_id
                    and receipt.document_digest == document_hash
                    and artifact_digest(checked_document) == document_hash
                    and receipt.source_digest == source_hash
                    and receipt.policy_hash == policy
                ):
                    answer.document, answer.verification = document, receipt
                    answer.steps = document_steps(document)
                    record["video_content_check"] = {
                        "reused": True,
                        **receipt.model_dump(mode="json"),
                    }
                    return receipt
        payload = user_context(
            sources.model_dump(mode="json"),
            role="answer_checker",
            instruction=answer.instruction,
            candidate_document=document.model_dump(mode="json"),
        )
        record["video_content_check_input"] = payload
        self.store.save("records", record["job_id"], record)
        check = self.call(
            "answer_checker",
            CHECK_INSTRUCTION,
            payload,
            AnswerCheck,
            budget,
        )
        record["video_content_check"] = {"reused": False, **check.model_dump()}
        self.store.save("records", record["job_id"], record)
        if check.decision != "pass":
            raise ContentError("content_uncertain", check.message, state=check.decision)
        receipt = VerifiedAnswer(
            answer_id=answer.answer_id,
            document_digest=document_hash,
            source_digest=source_hash,
            policy_hash=policy,
            checker_profile_hash=profile,
            report=check,
        )
        self.store.save(
            "answer_checks",
            answer.answer_id,
            {
                "document": document.model_dump(mode="json"),
                "verification": receipt.model_dump(mode="json"),
            },
        )
        answer.document, answer.verification = document, receipt
        answer.steps = document_steps(document)
        return receipt

    def check_plan(self, answer, plan, budget, record, repairs: RepairBudget):
        """Return the checked report and ordered scene IDs needing local revision."""
        report = self.call(
            "plan_checker",
            PLAN_PROMPT,
            user_context(
                {
                    "answer": answer.model_dump(
                        include={"title", "document", "verification", "warnings"}
                    )
                },
                role="plan_checker",
                effective_profile=answer.effective_profile,
                instruction=answer.instruction,
                plan=plan.model_dump(exclude={"plan_id", "answer_id", "answer_hash"}),
            ),
            PlanCheck,
            budget,
        )
        record.setdefault("plan_checks", []).append(
            {"plan_hash": digest(plan.model_dump_json()), **report.model_dump()}
        )
        self.store.save("records", record["job_id"], record)
        all_units = {unit.unit_id for unit in plan.teaching_units}
        scene_ids = {scene.scene_id for scene in plan.scenes}
        valid = (
            len(report.units) == len(all_units)
            and {unit.unit_id for unit in report.units} == all_units
            and all(
                unit.decision == "pass" and unit.scene_ids and set(unit.scene_ids) <= scene_ids
                for unit in report.units
            )
        )
        if report.decision == "pass" and valid and not report.problem_scene_ids:
            return report, []
        revisions = list(dict.fromkeys(report.problem_scene_ids))
        if report.decision != "revise" or not revisions or not repairs.available(len(revisions)):
            raise ContentError("video_plan_check", report.message, state="needs_review")
        return report, revisions
