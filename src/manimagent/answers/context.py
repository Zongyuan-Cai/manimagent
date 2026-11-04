"""Complete, source-checked context and conservative token budgeting."""

from __future__ import annotations

import json

from manimagent.config import Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.schemas import FileAsset, MaterialIR


class ContextAssembler:
    def __init__(self, store: ContentStore, settings: Settings):
        self.store = store
        self.settings = settings

    def build(self, material_ids: list[str], instruction: str) -> dict:
        materials = [
            self.store.load("materials", identifier, MaterialIR) for identifier in material_ids
        ]
        for material in materials:
            if any(b.review_state == "needs_review" or b.asset_ids for b in material.blocks):
                raise ContentError(
                    "content_uncertain",
                    "材料仍有待核对内容或未实现的图像输入",
                    state="needs_review",
                )
        return {
            "instruction": instruction,
            "materials": [
                {
                    **m.model_dump(),
                    "source_files": [
                        self.store.load("files", identifier, FileAsset).model_dump()
                        for identifier in m.source_file_ids
                    ],
                }
                for m in materials
            ],
            "allowed_source_refs": [
                {"material_id": m.material_id, "block_id": b.block_id}
                for m in materials
                for b in m.blocks
            ],
        }

    def check_budget(self, role: str, instruction: str, payload: dict, record: dict) -> None:
        settings = self.settings
        cfg = settings.model_for(role)
        # One token per UTF-8 byte is intentionally conservative for mixed Chinese/math input.
        estimate = (
            len((instruction + json.dumps(payload, ensure_ascii=False)).encode("utf-8")) + 512
        )
        total = estimate + cfg.max_output_tokens
        limit = min(cfg.context_window, settings.answer_context_tokens)
        record.setdefault("context_budgets", []).append(
            {
                "role": role,
                "input_estimate": estimate,
                "output_reserved": cfg.max_output_tokens,
                "limit": limit,
                "method": "utf8_bytes_upper_bound_v1",
            }
        )
        if total > limit:
            raise ContentError(
                "budget_exceeded",
                "完整材料与输出预留超过上下文预算，请缩小页码范围或拆分问题",
                state="needs_input",
            )
