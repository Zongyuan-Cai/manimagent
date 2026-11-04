"""Bound answer receipts and semantic reviews share one run's repair allowance."""

from __future__ import annotations

import json
from dataclasses import dataclass

from manimagent.answers.context import ContextAssembler
from manimagent.answers.prompts import CHECK_INSTRUCTION
from manimagent.answers.service import checker_profile_hash
from manimagent.content_store import read_json_bytes
from manimagent.errors import ContentError
from manimagent.schemas import AnswerCheck
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
        receipt_path = self.store.path("answer_checks", answer.answer_id)
        if receipt_path.exists():
            receipt = json.loads(read_json_bytes(receipt_path))
            if (
                receipt.get("answer_hash") == digest(answer.markdown)
                and receipt.get("policy_hash") == policy
                and receipt.get("checker_profile_hash") == profile
                and receipt.get("decision") == "pass"
            ):
                record["video_content_check"] = {"reused": True, **receipt}
                return
        bundle = ContextAssembler(self.store, self.settings).build(
            answer.material_ids, answer.instruction
        )
        check = self.call(
            "answer_checker",
            CHECK_INSTRUCTION,
            {**bundle, "candidate_markdown": answer.markdown},
            AnswerCheck,
            budget,
        )
        record["video_content_check"] = {"reused": False, **check.model_dump()}
        if check.decision != "pass":
            raise ContentError("content_uncertain", check.message, state=check.decision)
        self.store.save(
            "answer_checks",
            answer.answer_id,
            {
                "answer_hash": digest(answer.markdown),
                "policy_hash": policy,
                "checker_profile_hash": profile,
                "decision": "pass",
                "report": check.model_dump(),
            },
        )

    def check_plan(self, answer, plan, budget, record, repairs: RepairBudget):
        """Return the checked report and ordered scene IDs needing local revision."""
        report = self.call(
            "plan_checker",
            PLAN_PROMPT,
            {"answer": answer.model_dump(), "plan": plan.model_dump()},
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
