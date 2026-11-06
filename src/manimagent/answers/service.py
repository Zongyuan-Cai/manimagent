"""Answer generation, checking, source validation and immutable revisions."""

from __future__ import annotations

import hashlib
import json
import re

from markdown_it import MarkdownIt

from manimagent.answers.context import ContextAssembler
from manimagent.answers.prompts import CHECK_INSTRUCTION, WRITER_INSTRUCTION
from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.llm.client import ChatProvider
from manimagent.memory.service import MEMORY_CONTEXT_INSTRUCTION
from manimagent.schemas import (
    Answer,
    AnswerCheck,
    AnswerNotice,
    AnswerRequest,
    AnswerRevisionRequest,
    AnswerStep,
    SourceRef,
)


def checker_profile_hash(provider: ChatProvider) -> str:
    # ModelConfig contains endpoint/options and credential references, never keys.
    # Changing provider, model, thinking or output budget requires a fresh check.
    config = provider.settings.model_for("answer_checker").model_dump_json()
    return hashlib.sha256(config.encode("utf-8")).hexdigest()


def derive_answer(
    markdown: str,
    material_ids: list[str],
    instruction: str,
    bundle: dict,
    base_id: str | None = None,
    warnings: list[AnswerNotice] | None = None,
) -> Answer:
    allowed = {(r["material_id"], r["block_id"]) for r in bundle["allowed_source_refs"]}
    refs = []
    # Inspect every marker, including malformed tokens, instead of only matching valid IDs.
    markers = re.findall(r"\[\[(.*?)\]\]", markdown, flags=re.DOTALL)
    for marker in markers:
        pair = tuple(marker.split(":"))
        if pair not in allowed:
            raise ContentError(
                "invalid_reference",
                "答案包含不存在或格式无效的来源标记，请核对正文",
                state="needs_review",
            )
        ref = SourceRef(material_id=pair[0], block_id=pair[1])
        if ref not in refs:
            refs.append(ref)
    if not refs:
        raise ContentError(
            "invalid_reference", "答案缺少可核对的材料来源标记", state="needs_review"
        )
    if markdown.count("[[") != len(markers):
        raise ContentError("invalid_reference", "答案来源标记不完整", state="needs_review")
    tokens = MarkdownIt().parse(markdown)
    title = next(
        (
            tokens[index + 1].content.strip()
            for index, token in enumerate(tokens)
            if token.type == "heading_open" and index + 1 < len(tokens)
        ),
        "文字解答",
    )
    # A single lossless index is valid even for fenced code and multi-line display equations.
    step = AnswerStep(step_id="step_1", title=title, content=markdown, source_refs=refs)
    return Answer(
        answer_id=new_id("answer"),
        material_ids=material_ids,
        derived_from_answer_id=base_id,
        title=title,
        instruction=instruction,
        markdown=markdown,
        steps=[step],
        warnings=warnings or [],
    )


class AnswerService:
    def __init__(self, store: ContentStore, provider: ChatProvider, settings: Settings):
        self.store = store
        self.provider = provider
        self.settings = settings
        self.context = ContextAssembler(store, settings)

    def generate(self, request: AnswerRequest, budget: Budget, record: dict) -> Answer:
        bundle = self.context.build(request.material_ids, request.instruction)
        memory_snapshot = record.get("memory_snapshot")
        if memory_snapshot is not None:
            bundle["user_memory"] = memory_snapshot
        writer_instruction = WRITER_INSTRUCTION + (
            "\n" + MEMORY_CONTEXT_INSTRUCTION if memory_snapshot is not None else ""
        )
        record["context"] = bundle
        self.context.check_budget("answer_writer", writer_instruction, bundle, record)
        # Persist the exact context before the first model call, including failed attempts.
        self.store.save("records", record["job_id"], record)
        markdown = self.provider.complete_text("answer_writer", writer_instruction, bundle, budget)
        answer = derive_answer(markdown, request.material_ids, request.instruction, bundle)
        answer.memory_snapshot = memory_snapshot
        answer.warnings.extend(
            AnswerNotice(code=warning["code"], message=warning["message"])
            for warning in record.get("memory_warnings", [])
        )
        if self.settings.answer_check:
            check_payload = {**bundle, "candidate_markdown": markdown}
            check_prompt = CHECK_INSTRUCTION + json.dumps(
                AnswerCheck.model_json_schema(), ensure_ascii=False
            )
            self.context.check_budget("answer_checker", check_prompt, check_payload, record)
            check = self.provider.complete(
                "answer_checker", CHECK_INSTRUCTION, check_payload, AnswerCheck, budget
            )
            record["answer_check"] = check.model_dump()
            if check.decision != "pass":
                raise ContentError(
                    "source_incomplete" if check.decision == "needs_input" else "content_uncertain",
                    check.message,
                    state=check.decision,
                )
        else:
            answer.warnings.append(
                AnswerNotice(
                    code="content_check_disabled",
                    message="此回答未执行模型内容检查，请核对推理与结论",
                )
            )
        budget.remaining()
        if self.settings.answer_check:
            self.store.save(
                "answer_checks",
                answer.answer_id,
                {
                    "answer_hash": hashlib.sha256(answer.markdown.encode("utf-8")).hexdigest(),
                    "policy_hash": hashlib.sha256(CHECK_INSTRUCTION.encode("utf-8")).hexdigest(),
                    "checker_profile_hash": checker_profile_hash(self.provider),
                    "decision": "pass",
                    "report": record["answer_check"],
                },
            )
        self.store.save("answers", answer.answer_id, answer)
        return answer

    def revise(self, request: AnswerRevisionRequest) -> Answer:
        base = self.store.load("answers", request.base_answer_id, Answer)
        bundle = self.context.build(base.material_ids, base.instruction)
        warnings = [
            *base.warnings,
            *request.warnings,
            AnswerNotice(
                code="user_revision_unchecked",
                message="人工修订版本仅检查格式与来源，未重新执行模型内容检查",
            ),
        ]
        for warning in warnings:
            if any(
                ref.model_dump() not in bundle["allowed_source_refs"] for ref in warning.source_refs
            ):
                raise ContentError("invalid_reference", "修订提示包含无效来源引用")
            if any(step_id != "step_1" for step_id in warning.step_ids):
                raise ContentError("invalid_reference", "修订提示包含无效步骤引用")
        answer = derive_answer(
            request.markdown, base.material_ids, base.instruction, bundle, base.answer_id, warnings
        )
        answer.memory_snapshot = base.memory_snapshot
        self.store.save("answers", answer.answer_id, answer)
        return answer
