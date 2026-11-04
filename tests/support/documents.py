from __future__ import annotations

import io

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def pdf_bytes(text: str, paint: str = "") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    escaped = text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
    stream = DecodedStreamObject()
    raw = f"BT /F1 11 Tf 35 750 Td ({escaped}) Tj ET\n"
    if paint:
        raw += f"100 100 m 180 100 l 150 170 l {paint}\n"
    stream.set_data(raw.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def docx_bytes(text: str, special: str = "") -> bytes:
    doc = Document()
    if special == "superscript":
        p = doc.add_paragraph("The function is f(x)=x")
        p.add_run("2").font.superscript = True
        p.add_run("+1. Find f(3).")
    elif special == "subscript":
        p = doc.add_paragraph("a")
        p.add_run("2").font.subscript = True
        p.add_run("=7. Explain the indexed variable.")
    elif special == "symbol":
        p = doc.add_paragraph("An essential symbol: ")
        symbol = OxmlElement("w:sym")
        from docx.oxml.ns import qn

        symbol.set(qn("w:font"), "Symbol")
        symbol.set(qn("w:char"), "F061")
        p.add_run()._r.append(symbol)
    elif special in {"style_superscript", "inherited_subscript", "unused_style"}:
        style = doc.styles.add_style("ReviewMath", WD_STYLE_TYPE.CHARACTER)
        if special == "inherited_subscript":
            style.font.subscript = True
            child = doc.styles.add_style("ReviewChild", WD_STYLE_TYPE.CHARACTER)
            child.base_style = style
            style = child
        else:
            style.font.superscript = True
        p = doc.add_paragraph("Find the value for x=3. f(x)=x")
        run = p.add_run("^2" if special == "unused_style" else "2")
        if special != "unused_style":
            run.style = style
        p.add_run("+1.")
    else:
        doc.add_paragraph(text)
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Coverage"
        table.rows[0].cells[1].text = "Use all problem conditions above."
    result = io.BytesIO()
    doc.save(result)
    return result.getvalue()


def pdf_pages(texts):
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode("ascii") if text else b"")
        page[NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()
