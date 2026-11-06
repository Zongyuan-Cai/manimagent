"""Read Office documents as bounded, traceable material; never run macros/formulas."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from xml.etree import ElementTree

from manimagent.errors import ContentError

from .archive import inspect_archive
from .blocks import append_bounded, block


def parse_xlsx(path, selection, index, file_id, limits):
    from openpyxl import load_workbook

    names = inspect_archive(path, limits)
    issues, blocks, used = [], [], 0
    if any(name.startswith(("xl/drawings/", "xl/media/", "xl/charts/")) for name in names):
        issues.append("工作簿包含图片或图表，当前单元格读取不能核对其视觉内容")
    if any(name.startswith("xl/externalLinks/") for name in names):
        issues.append("工作簿包含外部链接，缓存值可能未更新，请确认后提供独立数据")
    if any(
        name.startswith(("xl/comments/", "xl/threadedComments/", "xl/comments")) for name in names
    ):
        issues.append("工作簿含批注或讨论，请将题目所需的批注另行粘贴，避免遗漏条件")
    # ZIP signature detection is authoritative; the stored asset intentionally has no .xlsx suffix.
    with path.open("rb") as source, path.open("rb") as cached_source:
        workbook = load_workbook(source, read_only=True, data_only=False, keep_links=False)
        cached = load_workbook(cached_source, read_only=True, data_only=True, keep_links=False)
        try:
            total = len(workbook.worksheets)
            if total > limits["max_parse_pages"]:
                raise ContentError(
                    "budget_exceeded", "工作表数量超限，请拆分工作簿", state="needs_input"
                )
            cells = 0
            for sheet_number, sheet in enumerate(workbook.worksheets, 1):
                # Read-only worksheets omit merge/layout metadata; retain the semantic ranges.
                xml = workbook._archive.read(sheet._worksheet_path)
                if b"<!DOCTYPE" in xml.upper():
                    raise ContentError(
                        "invalid_file", "工作表包含不支持的 XML 文档类型声明", state="needs_input"
                    )
                metadata = ElementTree.fromstring(xml)
                if metadata.findall(".//{*}vertAlign"):
                    issues.append(
                        f"工作表 {sheet.title} 含富文本上下标，请核对数学含义并补充明确文本"
                    )
                for merged in metadata.findall(".//{*}mergeCell"):
                    reference = merged.attrib.get("ref", "")
                    used = append_bounded(
                        blocks,
                        block(
                            f"工作表 {sheet_number} {sheet.title!r} · 合并单元格 {reference}（内容保存在左上单元格）",
                            index,
                            file_id,
                            {"sheet": sheet_number},
                        ),
                        limits,
                        used,
                    )
                # Declared dimensions can be enormous even in a tiny compressed document.
                cells += (sheet.max_row or 0) * (sheet.max_column or 0)
                if cells > limits["max_parse_chars"]:
                    raise ContentError(
                        "budget_exceeded",
                        "工作簿单元格范围过大，请删除远处空白格式或拆分表格",
                        state="needs_input",
                    )
                cache_rows = cached[sheet.title].iter_rows()
                for row, cached_row in zip(sheet.iter_rows(), cache_rows, strict=True):
                    for cell, cached_cell in zip(row, cached_row, strict=True):
                        if cell.value is None:
                            continue
                        value = cell.value
                        if isinstance(value, (date, datetime, time)):
                            value = value.isoformat()
                        content = f"工作表 {sheet_number} {sheet.title!r} · {cell.coordinate}："
                        if cell.data_type == "f":
                            content += f"公式 {value}；已保存计算值 {cached_cell.value!r}"
                            if cached_cell.value is None:
                                issues.append(
                                    f"工作表 {sheet.title} 的 {cell.coordinate} 公式没有计算缓存；请在 Excel 中重新计算并保存，或提供明确数值"
                                )
                        else:
                            content += str(value)
                        if cell.number_format != "General":
                            content += f"（显示格式：{cell.number_format}）"
                        used = append_bounded(
                            blocks,
                            block(
                                content,
                                index,
                                file_id,
                                {"sheet": sheet_number, "row": cell.row, "column": cell.column},
                            ),
                            limits,
                            used,
                        )
            coverage = {
                "input_index": index,
                "requested_scope": "full",
                "state": "complete",
                "unit_kind": "sheet",
                "total_units": total,
                "processed_ranges": [[1, total]],
                "missing_ranges": [],
            }
            return blocks, coverage, list(dict.fromkeys(issues))
        finally:
            workbook.close()
            cached.close()


def parse_pptx(path, selection, index, file_id, limits):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from manimagent.materials.visual_assets import image_visual

    inspect_archive(path, limits)
    presentation = Presentation(path)
    total = len(presentation.slides)
    start, end = (selection["start"], selection["end"]) if selection else (1, total)
    if not total or start > total or end > total:
        raise ContentError("invalid_selection", "范围超出实际幻灯片数量", state="needs_input")
    if end - start + 1 > limits["max_parse_pages"]:
        raise ContentError("budget_exceeded", "幻灯片数量超限，请选择更小范围", state="needs_input")
    blocks, issues, visuals, used = [], [], [], 0
    for slide_number in range(start, end + 1):
        slide = presentation.slides[slide_number - 1]

        def visit(shapes):
            for shape in shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    yield from visit(shape.shapes)
                else:
                    yield shape

        inherited = [
            shape
            for container in (slide.slide_layout, slide.slide_layout.slide_master)
            for shape in container.shapes
            if not shape.is_placeholder
        ]
        # Non-placeholder text/images in a layout or master are visible on the slide too.
        for shape_number, shape in enumerate(visit([*slide.shapes, *inherited]), 1):
            location = {"slide": slide_number, "shape": shape_number}
            if any(node.tag.endswith("}oMath") for node in shape.element.iter()):
                issues.append(f"第 {slide_number} 页包含 Office 公式对象，请核对并补充明确公式")
            if shape.has_text_frame and shape.text.strip():
                content = f"幻灯片 {slide_number} · 对象 {shape_number}\n{shape.text}"
                used = append_bounded(
                    blocks, block(content, index, file_id, location), limits, used
                )
            if shape.has_table:
                for row_number, row in enumerate(shape.table.rows, 1):
                    values = [cell.text for cell in row.cells]
                    content = f"幻灯片 {slide_number} · 表格 {shape_number} · 行 {row_number}：{json.dumps(values, ensure_ascii=False)}"
                    used = append_bounded(
                        blocks,
                        block(content, index, file_id, {**location, "row": row_number}, "table"),
                        limits,
                        used,
                    )
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                visuals.append(
                    image_visual(
                        shape.image.blob, location, f"幻灯片 {slide_number} · 图片 {shape_number}"
                    )
                )
                if len(visuals) > limits["max_visual_pages"]:
                    raise ContentError(
                        "budget_exceeded",
                        "幻灯片图片数量超过视觉读取上限，请选择更小范围",
                        state="needs_input",
                    )
            elif (
                shape.shape_type
                in {
                    MSO_SHAPE_TYPE.LINKED_PICTURE,
                    MSO_SHAPE_TYPE.MEDIA,
                    MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT,
                    MSO_SHAPE_TYPE.LINKED_OLE_OBJECT,
                }
                or shape.has_chart
            ):
                issues.append(
                    f"第 {slide_number} 页包含图片、图表或嵌入对象，文字提取不能核对其内容"
                )
            elif shape.shape_type == MSO_SHAPE_TYPE.IGX_GRAPHIC:
                issues.append(f"第 {slide_number} 页包含 SmartArt 图示，需要核对视觉内容")
            elif shape.shape_type in {
                MSO_SHAPE_TYPE.AUTO_SHAPE,
                MSO_SHAPE_TYPE.LINE,
                MSO_SHAPE_TYPE.FREEFORM,
            }:
                issues.append(
                    f"第 {slide_number} 页包含几何图形或连线，文字读取不能完整核对图形关系；请另存为 PDF 或提供截图"
                )
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes and notes.text.strip():
                used = append_bounded(
                    blocks,
                    block(
                        f"幻灯片 {slide_number} · 讲者备注\n{notes.text}",
                        index,
                        file_id,
                        {"slide": slide_number, "notes": 1},
                    ),
                    limits,
                    used,
                )
    coverage = {
        "input_index": index,
        "requested_scope": selection or "full",
        "state": "complete",
        "unit_kind": "slide",
        "total_units": total,
        "processed_ranges": [[start, end]],
        "missing_ranges": [],
    }
    return blocks, coverage, list(dict.fromkeys(issues)), visuals
