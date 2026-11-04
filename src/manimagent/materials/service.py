"""Material validation, complete-source assembly and publication."""

from __future__ import annotations

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.materials.parsers import text_blocks
from manimagent.materials.process import parse_isolated
from manimagent.schemas import FileInput, MaterialBlock, MaterialIR, ParseRequest


class MaterialService:
    def __init__(self, store: ContentStore, settings: Settings):
        self.store = store
        self.settings = settings

    def validate(self, request: ParseRequest) -> None:
        for item in request.inputs:
            if isinstance(item, FileInput):
                asset = self.store.verify_file(item.file_id)
                if request.input_mode == "native":
                    raise ContentError(
                        "unsupported_file_input", "尚未实现原生文件读取，请使用 auto 或 local", 422
                    )
                if item.selection and (
                    asset.media_type != "application/pdf" or item.selection.kind != "pages"
                ):
                    raise ContentError("invalid_selection", "当前仅 PDF 支持 pages 范围选择")

    def parse(self, request: ParseRequest, budget: Budget, record: dict) -> MaterialIR:
        self.validate(request)
        blocks, files = [], []
        record["parse"] = {
            "request": request.model_dump(include=set(ParseRequest.model_fields)),
            "parser_version": "local-1",
            "coverage": [],
        }
        for index, item in enumerate(request.inputs):
            budget.remaining()
            if item.type == "text":
                blocks.extend(text_blocks(item.text, index))
                record["parse"]["coverage"].append(
                    {
                        "input_index": index,
                        "state": "complete",
                        "unit_kind": "line",
                        "total_units": len(item.text.splitlines()),
                        "processed_ranges": [[1, len(item.text.splitlines())]],
                        "missing_ranges": [],
                    }
                )
                continue
            asset = self.store.verify_file(item.file_id)
            files.append(item.file_id)
            limits = {
                name: getattr(self.settings, name)
                for name in (
                    "max_parse_pages",
                    "max_parse_chars",
                    "max_archive_entries",
                    "max_archive_bytes",
                )
            }
            candidate = parse_isolated(
                {
                    "path": str(self.store.file_path(item.file_id)),
                    "media_type": asset.media_type,
                    "selection": item.selection.model_dump() if item.selection else None,
                    "index": index,
                    "file_id": item.file_id,
                    "limits": limits,
                },
                self.store.root,
                self.settings.parse_timeout,
                budget,
            )
            record["parse"]["coverage"].append(candidate["coverage"])
            record["parse"].setdefault("candidates", []).append(candidate)
            if not candidate["blocks"] and asset.media_type.startswith("text/"):
                raise ContentError(
                    "source_incomplete", "文件没有可用正文，请提供完整文字材料", state="needs_input"
                )
            if candidate["issues"]:
                raise ContentError(
                    "content_uncertain", "；".join(candidate["issues"]), state="needs_review"
                )
            blocks.extend(MaterialBlock.model_validate(block) for block in candidate["blocks"])
        if not blocks:
            raise ContentError(
                "source_incomplete", "材料中没有正文，请补充输入", state="needs_input"
            )
        if sum(len(b.content) for b in blocks) > self.settings.max_parse_chars:
            raise ContentError("budget_exceeded", "材料总量超限，请缩小范围", state="needs_input")
        result = MaterialIR(
            material_id=new_id("material"),
            source_file_ids=list(dict.fromkeys(files)),
            blocks=blocks,
        )
        budget.remaining()
        self.store.save("materials", result.material_id, result)
        return result
