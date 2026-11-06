from __future__ import annotations

import io

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from PIL import Image

from manimagent.materials.capabilities import DOCX
from manimagent.materials.parsers import parse_file

LIMITS = {
    "max_archive_entries": 2000,
    "max_archive_bytes": 50 * 1024 * 1024,
    "max_parse_chars": 200000,
    "max_parse_pages": 100,
    "max_visual_pages": 12,
}


def run(text):
    return "<m:r><m:t>" + text + "</m:t></m:r>"


def argument(name, text):
    return f"<m:{name}>" + run(text) + f"</m:{name}>"


def math(content):
    return parse_xml(f"<m:oMath {nsdecls('m', 'w')}>{content}</m:oMath>")


def read(document, tmp_path):
    path = tmp_path / "equations.docx"
    document.save(path)
    return parse_file(path, DOCX, None, 0, "file_" + "a" * 32, LIMITS)


@pytest.mark.parametrize(
    "xml,expected",
    [
        ("<m:f>" + argument("num", "x+1") + argument("den", "2") + "</m:f>", r"\frac{x+1}{2}"),
        ("<m:sSup>" + argument("e", "x") + argument("sup", "2") + "</m:sSup>", "{x}^{2}"),
        (
            "<m:sSubSup>"
            + argument("e", "a")
            + argument("sub", "i")
            + argument("sup", "2")
            + "</m:sSubSup>",
            "{a}_{i}^{2}",
        ),
        ("<m:rad>" + argument("deg", "3") + argument("e", "x") + "</m:rad>", r"\sqrt[3]{x}"),
        ("<m:d>" + argument("e", "a") + argument("e", "b") + "</m:d>", r"\left( a|b\right) "),
        (
            '<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr>'
            + argument("sub", "i=1")
            + argument("sup", "n")
            + argument("e", "x")
            + "</m:nary>",
            r"\sum_{i=1}^{n} x",
        ),
        (
            '<m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr>'
            + argument("sub", "0")
            + argument("sup", "1")
            + argument("e", "x dx")
            + "</m:nary>",
            r"\int_{0}^{1} x dx",
        ),
        (
            "<m:m><m:mr>"
            + argument("e", "1")
            + argument("e", "2")
            + "</m:mr><m:mr>"
            + argument("e", "3")
            + argument("e", "4")
            + "</m:mr></m:m>",
            r"\begin{matrix}1 & 2 \\ 3 & 4\end{matrix}",
        ),
    ],
)
def test_real_docx_preserves_common_math_and_surrounding_text(tmp_path, xml, expected):
    document = Document()
    paragraph = document.add_paragraph("已知 ")
    paragraph._p.append(math(xml))
    paragraph.add_run("，请解释。")
    result = read(document, tmp_path)
    assert result["issues"] == []
    assert result["coverage"]["state"] == "complete"
    assert result["blocks"][0]["content"] == "已知 $" + expected + "$，请解释。"
    assert result["blocks"][0]["source"]["locator"] == {"paragraph": 1}


def test_docx_table_formula_and_explicit_superscript_groups_keep_order(tmp_path):
    document = Document()
    paragraph = document.add_paragraph("f(x)=x")
    paragraph.add_run("2").font.superscript = True
    paragraph.add_run("3").font.superscript = True
    paragraph.add_run("+1")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "a"
    table.cell(0, 0).paragraphs[0].add_run("i").font.subscript = True
    table.cell(0, 1).paragraphs[0]._p.append(
        math("<m:f>" + argument("num", "1") + argument("den", "2") + "</m:f>")
    )
    document.add_paragraph("结束")
    result = read(document, tmp_path)
    assert not result["issues"]
    assert [block["kind"] for block in result["blocks"]] == ["text", "table", "text"]
    assert result["blocks"][0]["content"] == "f(x)=x^{23}+1"
    assert r"| a_{i} | $\frac{1}{2}$ |" in result["blocks"][1]["content"]
    assert result["blocks"][2]["content"] == "结束"


@pytest.mark.parametrize(
    "xml",
    [
        "<m:phant>" + argument("e", "important") + "</m:phant>",
        "<m:f>" + argument("num", "1") + "</m:f>",
        "<m:nary>" + argument("e", "x") + "</m:nary>",
        "",
    ],
)
def test_unknown_or_incomplete_omml_is_reported_not_flattened(tmp_path, xml):
    document = Document()
    paragraph = document.add_paragraph("正文仍可诊断 ")
    paragraph._p.append(math(xml))
    result = read(document, tmp_path)
    assert result["issues"]
    assert result["coverage"]["state"] == "incomplete"
    assert "important" not in result["blocks"][0]["content"]


def picture_document():
    image = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(image, format="PNG")
    image.seek(0)
    document = Document()
    document.add_paragraph("请解释下图")
    document.add_picture(image)
    return document


def test_real_docx_embedded_picture_is_prepared_with_source_and_placeholder(tmp_path):
    document = picture_document()
    blip = document.element.xpath(".//a:blip")[0]
    blip.append(
        parse_xml(
            f'<a:extLst {nsdecls("a")}><a:ext uri="dpi"><a14:useLocalDpi xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" val="0"/></a:ext></a:extLst>'
        )
    )
    result = read(document, tmp_path)
    assert not result["issues"]
    assert len(result["visuals"]) == 1
    visual = result["visuals"][0]
    assert visual["media_type"] == "image/jpeg" and visual["data"]
    assert visual["locator"] == {"image": 1, "body_item": 2}
    assert "[图片 1" in result["blocks"][1]["content"]


@pytest.mark.parametrize("mutation", ["external", "rotation", "complex"])
def test_complex_docx_drawing_is_not_replaced_by_its_picture_preview(tmp_path, mutation):
    document = picture_document()
    drawing = document.element.xpath(".//w:drawing")[0]
    if mutation == "external":
        drawing.xpath(".//a:blip")[0].set(qn("r:link"), "external-rId")
    elif mutation == "rotation":
        drawing.xpath(".//a:xfrm")[0].set("rot", "5400000")
    else:
        drawing.xpath(".//a:graphicData")[0].append(parse_xml(f"<a:graphic {nsdecls('a')}/>"))
    result = read(document, tmp_path)
    assert result["issues"] and result["coverage"]["state"] == "incomplete"
    assert not result["visuals"]
