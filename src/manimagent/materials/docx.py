"""Read Word body content and prepare supported embedded images for visual reading."""

from __future__ import annotations

from pathlib import Path

from manimagent.content_store import new_id
from manimagent.errors import ContentError
from manimagent.schemas import MaterialBlock, SourceLocation

from .archive import inspect_archive
from .blocks import coverage_for


def parse_docx(path: Path, selection: dict | None, index: int, file_id: str, limits: dict):
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.ns import qn
    from docx.table import Table

    from manimagent.materials.docx_math import paragraph_text
    from manimagent.materials.visual_assets import image_visual

    blocks, issues, visuals = [], [], []
    drawing_labels = {}
    coverage = coverage_for(index, selection)
    names = inspect_archive(path, limits)
    extra_parts = ("word/header", "word/footer", "word/footnotes", "word/endnotes")
    if any(name.startswith(extra_parts) for name in names):
        issues.append("文档包含页眉、页脚或脚注，请核对并将必要内容粘贴为文本")
    document = Document(path)
    root = document.element
    if root.xpath(
        ".//w:object | .//w:pict | .//w:txbxContent | .//w:altChunk | .//w:ins | .//w:del | .//w:sdt"
    ):
        issues.append("DOCX 包含图片、嵌入对象或特殊内容，当前本地解析不能保证完整，请核对原件")
    body_items = list(document.element.body)

    def plain_blip(blip):
        # Word commonly writes this DPI hint. It changes raster sizing, not content.
        for child in blip:
            if child.tag != qn("a:extLst"):
                return False
            for extension in child:
                if extension.tag != qn("a:ext") or not list(extension):
                    return False
                for setting in extension:
                    if (
                        setting.tag
                        != "{http://schemas.microsoft.com/office/drawing/2010/main}useLocalDpi"
                    ):
                        return False
        return True

    for image_index, drawing in enumerate(root.xpath(".//w:drawing"), 1):
        pictures = drawing.xpath(".//pic:pic")
        data = drawing.xpath(".//a:graphicData")
        blips = drawing.xpath(".//a:blip")
        # A raster inside a complex drawing does not represent the whole chart,
        # group, crop or transformed shape; don't silently replace it with a preview.
        simple = (
            len(pictures) == len(data) == len(blips) == 1
            and list(data[0]) == [pictures[0]]
            and data[0].get("uri") == "http://schemas.openxmlformats.org/drawingml/2006/picture"
            and not blips[0].get(qn("r:link"))
            and plain_blip(blips[0])
            and not drawing.xpath(".//a:custGeom | .//a:effectDag")
            and not any(list(effect) for effect in drawing.xpath(".//a:effectLst"))
            and all(geom.get("prst") == "rect" for geom in drawing.xpath(".//a:prstGeom"))
            and all(
                value in {"0", "false"}
                for crop in drawing.xpath(".//a:srcRect")
                for value in crop.attrib.values()
            )
            and all(
                xfrm.get(attr, "0") in {"0", "false"}
                for xfrm in drawing.xpath(".//a:xfrm")
                for attr in ("rot", "flipH", "flipV")
            )
        )
        relation = document.part.rels.get(blips[0].get(qn("r:embed"), "")) if simple else None
        if relation is None or relation.is_external or relation.reltype != RELATIONSHIP_TYPE.IMAGE:
            issues.append(
                f"DOCX 第 {image_index} 个绘图含外链或复杂/未知对象，无法可靠读取，请核对原件"
            )
            continue
        if len(visuals) >= limits["max_visual_pages"]:
            raise ContentError(
                "budget_exceeded", "DOCX 图片数量超出视觉读取上限，请拆分材料", state="needs_input"
            )
        label = f"DOCX 图片 {image_index}"
        ancestor = drawing
        while ancestor.getparent() is not None and ancestor.getparent() != document.element.body:
            ancestor = ancestor.getparent()
        locator = {"image": image_index}
        if ancestor in body_items:
            locator["body_item"] = body_items.index(ancestor) + 1
        try:
            visuals.append(image_visual(relation.target_part.blob, locator, label))
        except (OSError, ValueError):
            issues.append(f"{label}不是可解码的静态图片，请转为 PNG/JPEG 后提交")
            continue
        drawing_labels[drawing] = f"[图片 {image_index}，另见图像读取内容]"
    # Paragraph.text drops math-relevant run formatting and legacy symbol glyphs.
    # Do not let a model guess whether flattened 'x2' meant x squared or 2*x.
    if root.xpath(".//w:sym"):
        issues.append("DOCX 包含依赖专用字体的符号，请提供明确的文本或 LaTeX")
    # Formatting can be inherited from a used character/paragraph/table style.
    # Ignore unused styles: a template may contain many unrelated math styles.
    if document.styles.element.xpath(
        "./w:docDefaults/w:rPrDefault/w:rPr/w:vertAlign[@w:val='superscript' or @w:val='subscript']"
    ):
        issues.append("DOCX 文档默认格式包含上下标，请提供明确的文本或 LaTeX")
    styles = {style.style_id: style for style in document.styles}
    pending = list(root.xpath(".//w:rStyle/@w:val | .//w:pStyle/@w:val | .//w:tblStyle/@w:val"))
    pending.extend(
        style.get(qn("w:styleId"))
        for style in document.styles.element.xpath(
            "./w:style[@w:default='1' or @w:default='true' or @w:default='on']"
        )
    )
    visited = set()
    while pending:
        style_id = pending.pop()
        if style_id in visited or style_id not in styles:
            continue
        visited.add(style_id)
        style = styles[style_id]
        if style.element.xpath(".//w:vertAlign[@w:val='superscript' or @w:val='subscript']"):
            issues.append("DOCX 使用的样式包含上下标，纯文本会丢失数学含义，请提供文本或 LaTeX")
            break
        pending.extend(style.element.xpath("./w:basedOn/@w:val"))
    paragraph = 0
    units = 0
    for item in document.iter_inner_content():
        units += 1
        if isinstance(item, Table):
            if item._element.xpath(".//w:tbl"):
                issues.append("DOCX 包含嵌套表格，请转为普通表格或文本后提交")
            rows = []
            for row in item.rows:
                cells = []
                for cell in row.cells:
                    values = []
                    for entry in cell.paragraphs:
                        value, problems = paragraph_text(entry, drawing_labels)
                        values.append(value)
                        issues.extend(problems)
                    cells.append("\n".join(values).replace("|", "\\|").replace("\n", "<br>"))
                rows.append(cells)
            if not rows:
                continue
            width = max(map(len, rows))
            rows = [row + [""] * (width - len(row)) for row in rows]
            lines = ["| " + " | ".join(row) + " |" for row in rows]
            lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
            blocks.append(
                MaterialBlock(
                    block_id=new_id("block"),
                    kind="table",
                    content="\n".join(lines),
                    source=SourceLocation(
                        input_index=index,
                        file_id=file_id,
                        locator=None,
                        verification="unavailable",
                    ),
                )
            )
        else:
            paragraph += 1
            content, problems = paragraph_text(item, drawing_labels)
            issues.extend(problems)
            if content.strip():
                blocks.append(
                    MaterialBlock(
                        block_id=new_id("block"),
                        kind="heading"
                        if item.style and item.style.name.startswith("Heading")
                        else "text",
                        content=content,
                        source=SourceLocation(
                            input_index=index, file_id=file_id, locator={"paragraph": paragraph}
                        ),
                    )
                )
    coverage.update(
        unit_kind="body_item", total_units=units, processed_ranges=[[1, units]] if units else []
    )
    return blocks, coverage, issues, visuals
