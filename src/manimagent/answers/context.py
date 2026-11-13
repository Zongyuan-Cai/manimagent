"""Freeze complete, source-checked materials for one teaching request."""

from __future__ import annotations

from manimagent.config import Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.schemas import (
    FileAsset,
    MaterialIR,
    SourceBlock,
    SourceBundle,
    SourceFile,
    SourceMaterial,
    SourceRef,
    SourceWarning,
)


class ContextAssembler:
    def __init__(self, store: ContentStore, settings: Settings):
        self.store = store

    def build_bundle(self, material_ids: list[str]) -> SourceBundle:
        materials = [
            self.store.load("materials", identifier, MaterialIR) for identifier in material_ids
        ]
        if len(material_ids) != len(set(material_ids)):
            raise ContentError("invalid_reference", "材料 ID 不能重复")
        for material in materials:
            if any(b.review_state == "needs_review" or b.asset_ids for b in material.blocks):
                raise ContentError(
                    "content_uncertain",
                    "材料仍有待核对内容或未实现的图像输入",
                    state="needs_review",
                )
        return SourceBundle(
            materials=tuple(
                SourceMaterial(
                    material_id=m.material_id,
                    blocks=tuple(
                        SourceBlock(
                            block_id=b.block_id,
                            kind=b.kind,
                            content=b.content,
                            input_index=b.source.input_index,
                            file_id=b.source.file_id,
                            locator=tuple(sorted((b.source.locator or {}).items())),
                            verification=b.source.verification,
                            review_state=b.review_state,
                        )
                        for b in m.blocks
                    ),
                    warnings=tuple(
                        SourceWarning.model_validate(w.model_dump()) for w in m.warnings
                    ),
                    source_files=tuple(
                        SourceFile.model_validate(
                            self.store.load("files", identifier, FileAsset).model_dump(
                                include={"file_id", "filename", "media_type"}
                            )
                        )
                        for identifier in m.source_file_ids
                    ),
                )
                for m in materials
            ),
            allowed_source_refs=tuple(
                SourceRef(material_id=m.material_id, block_id=b.block_id)
                for m in materials
                for b in m.blocks
            ),
        )

    def build(self, material_ids: list[str]) -> dict:
        return self.build_bundle(material_ids).model_dump(mode="json")
