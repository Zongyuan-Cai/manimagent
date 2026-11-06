"""Material validation, complete-source assembly and publication."""

from __future__ import annotations

from manimagent.budget import Budget, BudgetExceeded
from manimagent.config import Settings
from manimagent.content_store import ContentStore, new_id
from manimagent.errors import ContentError
from manimagent.materials.blocks import text_blocks
from manimagent.materials.capabilities import PPTX
from manimagent.materials.process import parse_isolated
from manimagent.materials.vision import read_visual
from manimagent.schemas import FileInput, MaterialBlock, MaterialIR, Notice, ParseRequest


class MaterialService:
    def __init__(self, store: ContentStore, settings: Settings, provider=None):
        self.store = store
        self.settings = settings
        self.provider = provider

    def validate(self, request: ParseRequest) -> None:
        for item in request.inputs:
            if isinstance(item, FileInput):
                asset = self.store.verify_file(item.file_id)
                if request.input_mode == "native":
                    raise ContentError(
                        "unsupported_file_input", "尚未实现原生文件读取，请使用 auto 或 local", 422
                    )
                if item.selection and (asset.media_type, item.selection.kind) not in {
                    ("application/pdf", "pages"),
                    (PPTX, "slides"),
                }:
                    raise ContentError("invalid_selection", "范围选择仅支持 PDF 页码或 PPTX 幻灯片")

    def parse(self, request: ParseRequest, budget: Budget, record: dict) -> MaterialIR:
        self.validate(request)
        blocks, files, warnings = [], [], []
        visual_count = 0
        record["parse"] = {
            "request": request.model_dump(include=set(ParseRequest.model_fields)),
            "parser_version": "multiformat-vision-2",
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
                    "max_visual_pages",
                )
            }
            try:
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
                visuals = candidate.pop("visuals")
                candidate["visual_inputs"] = [
                    {key: value for key, value in visual.items() if key != "data"}
                    for visual in visuals
                ]
                record["parse"]["coverage"].append(candidate["coverage"])
                record["parse"].setdefault("candidates", []).append(candidate)
                if not candidate["blocks"] and asset.media_type.startswith("text/"):
                    raise ContentError(
                        "source_incomplete",
                        "文件没有可用正文，请提供完整文字材料",
                        state="needs_input",
                    )
                if candidate["issues"]:
                    raise ContentError(
                        "content_uncertain", "；".join(candidate["issues"]), state="needs_review"
                    )
                if visuals:
                    if request.input_mode == "local":
                        raise ContentError(
                            "vision_required",
                            "此材料需要视觉读取；请选择自动读取，或提供完整文本",
                            state="needs_input",
                        )
                    visual_count += len(visuals)
                    if visual_count > self.settings.max_visual_pages:
                        raise ContentError(
                            "budget_exceeded",
                            "本次材料需要视觉读取的图片/页数超限，请选页或拆分提交",
                            state="needs_input",
                        )
                    for visual_index, visual in enumerate(visuals):
                        try:
                            block, issues = read_visual(
                                visual,
                                provider=self.provider,
                                budget=budget,
                                index=index,
                                file_id=item.file_id,
                                pending_visuals=len(visuals) - visual_index - 1,
                            )
                        except BudgetExceeded as exc:
                            raise ContentError(
                                "budget_exceeded",
                                "材料读取达到本次预算上限，请缩小范围后重试",
                                state="needs_input",
                            ) from exc
                        candidate["blocks"].append(block.model_dump())
                        if issues:
                            candidate["coverage"]["state"] = "incomplete"
                            candidate["issues"].extend(issues)
                            raise ContentError(
                                "content_uncertain", "；".join(issues), state="needs_review"
                            )
                        warnings.append(
                            Notice(
                                code="visual_reading",
                                message=f"{asset.filename} {visual['label']}使用视觉模型读取，请核对原件中的公式、数字和图形条件。",
                                block_ids=[block.block_id],
                            )
                        )
                    candidate["coverage"]["reading_method"] = "visual_model"
            except ContentError as exc:
                error = ContentError(
                    exc.error.code,
                    f"{asset.filename}：{exc.error.message}",
                    exc.http_status,
                    exc.state,
                )
                error.error.details = {
                    **exc.error.details,
                    "filename": asset.filename,
                    "file_id": asset.file_id,
                    "input_index": index,
                }
                error.error.retryable = exc.error.retryable
                raise error from exc
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
            warnings=warnings,
        )
        budget.remaining()
        self.store.save("materials", result.material_id, result)
        return result
