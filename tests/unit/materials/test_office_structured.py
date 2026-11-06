from io import BytesIO

import pytest

from manimagent.config import Settings
from manimagent.content_store import ContentStore
from manimagent.errors import ContentError
from manimagent.materials.capabilities import PPTX, XLSX
from manimagent.materials.parsers import parse_file


def read_material(tmp_path, filename, data, selection=None, **changes):
    settings = Settings(**changes)
    store = ContentStore(tmp_path, settings.max_upload_bytes)
    asset = store.put_file(BytesIO(data), filename)
    limits = {
        name: getattr(settings, name)
        for name in (
            "max_parse_pages",
            "max_parse_chars",
            "max_archive_entries",
            "max_archive_bytes",
            "max_visual_pages",
        )
    }
    return asset, parse_file(
        store.file_path(asset.file_id), asset.media_type, selection, 0, asset.file_id, limits
    )


def test_csv_keeps_quoted_multiline_fields_and_empty_columns(tmp_path):
    asset, result = read_material(
        tmp_path, "data.csv", b'name,note,empty\nA,"line one\nline two",\n'
    )
    assert asset.media_type == "text/csv"
    assert len(result["blocks"]) == 2 and not result["issues"]
    assert '"A", "line one\\nline two", ""' in result["blocks"][1]["content"]
    assert result["blocks"][1]["source"]["locator"] == {"row": 2}


def test_json_preserves_large_number_spelling_and_jsonl_rejects_bad_lines(tmp_path):
    raw = b'{"measurement":1.234567890123456789,"count":12345678901234567890}'
    _, result = read_material(tmp_path, "data.json", raw)
    assert result["blocks"][0]["content"] == raw.decode()
    with pytest.raises(ContentError, match="结构无效"):
        read_material(tmp_path, "data.jsonl", b'{"ok":1}\nnot-json\n')


def test_source_code_is_read_as_text_and_never_executed(tmp_path):
    target = tmp_path / "should-not-exist"
    code = f"from pathlib import Path\nPath({str(target)!r}).write_text('bad')"
    _, result = read_material(tmp_path, "script.py", code.encode())
    assert code in result["blocks"][0]["content"]
    assert not target.exists()


def test_source_code_keeps_first_line_indentation_and_paragraph_line_locations(tmp_path):
    text = "    if ready:\n        run()\n\n\telsewhere()\n"
    _, result = read_material(tmp_path, "fragment.py", text.encode())
    assert [item["content"] for item in result["blocks"]] == [
        "    if ready:\n        run()",
        "\telsewhere()",
    ]
    assert [item["source"]["locator"] for item in result["blocks"]] == [
        {"line_start": 1, "line_end": 2},
        {"line_start": 4, "line_end": 4},
    ]


@pytest.mark.parametrize("mime", ["application/json", "application/vnd.plotly.v1+json"])
def test_notebook_does_not_claim_complete_coverage_for_unread_rich_outputs(tmp_path, mime):
    import json

    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": "display(result)",
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {mime: {"value": 4}, "text/plain": "<Result>"},
                    }
                ],
            }
        ]
    }
    _, result = read_material(tmp_path, "result.ipynb", json.dumps(notebook).encode())
    assert result["coverage"]["state"] == "incomplete"
    assert any("富媒体" in issue for issue in result["issues"])


def test_notebook_preserves_saved_latex_output(tmp_path):
    import json

    formula = r"$x=\frac{1}{2}$"
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": "display(solution)",
                "outputs": [{"output_type": "display_data", "data": {"text/latex": formula}}],
            }
        ]
    }
    _, result = read_material(tmp_path, "math.ipynb", json.dumps(notebook).encode())
    assert not result["issues"]
    assert formula in result["blocks"][1]["content"]


def test_notebook_reads_cells_outputs_and_reports_unread_images(tmp_path):
    import json

    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print(2+2)"],
                "outputs": [
                    {"output_type": "stream", "text": ["4\n"]},
                    {"output_type": "display_data", "data": {"image/png": "not-an-image"}},
                ],
            }
        ]
    }
    _, result = read_material(tmp_path, "lesson.ipynb", json.dumps(notebook).encode())
    assert "print(2+2)" in result["blocks"][0]["content"]
    assert "4\n" in result["blocks"][1]["content"]
    assert result["blocks"][1]["source"]["locator"] == {"cell": 1, "output": 1}
    assert result["issues"] and result["coverage"]["state"] == "incomplete"


def workbook_bytes(configure):
    from openpyxl import Workbook

    book = Workbook()
    configure(book.active)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_xlsx_keeps_sheet_cells_zeroes_dates_and_merged_ranges(tmp_path):
    from datetime import date

    def setup(sheet):
        sheet.title = "成绩"
        sheet["A1"], sheet["B2"], sheet["C2"] = "合并标题", 0, date(2026, 11, 6)
        sheet["D2"], sheet["D2"].number_format = 0.5, "0%"
        sheet.merge_cells("A1:C1")

    asset, result = read_material(tmp_path, "table.xlsx", workbook_bytes(setup))
    assert asset.media_type == XLSX and not result["issues"]
    text = "\n".join(item["content"] for item in result["blocks"])
    assert "合并单元格 A1:C1" in text and "B2：0" in text and "2026-11-06" in text
    assert "D2：0.5（显示格式：0%）" in text
    value = next(item for item in result["blocks"] if "B2：0" in item["content"])
    assert value["source"]["locator"] == {"sheet": 1, "row": 2, "column": 2}


def test_xlsx_formula_without_cached_value_requests_review(tmp_path):
    data = workbook_bytes(lambda sheet: setattr(sheet["A1"], "value", "=1+1"))
    _, result = read_material(tmp_path, "formula.xlsx", data)
    assert "=1+1" in result["blocks"][0]["content"]
    assert "没有计算缓存" in result["issues"][0]
    assert result["coverage"]["state"] == "incomplete"


def test_xlsx_sparse_huge_dimension_is_rejected_before_iteration(tmp_path):
    data = workbook_bytes(lambda sheet: setattr(sheet["XFD1048576"], "value", "far away"))
    with pytest.raises(ContentError, match="单元格范围过大"):
        read_material(tmp_path, "sparse.xlsx", data)


def slides_bytes(include_image=False):
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    for title in ("第一页", "第二页"):
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = title
    slide = deck.slides[1]
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(5), Inches(1)).table
    table.cell(0, 0).text, table.cell(1, 1).text = "label", "0"
    slide.notes_slide.notes_text_frame.text = "讲者备注：先解释依据"
    if include_image:
        picture = BytesIO()
        Image.new("RGB", (32, 32), "white").save(picture, format="PNG")
        picture.seek(0)
        slide.shapes.add_picture(picture, Inches(1), Inches(4), width=Inches(1))
    output = BytesIO()
    deck.save(output)
    return output.getvalue()


def test_pptx_selection_preserves_text_table_and_notes(tmp_path):
    asset, result = read_material(
        tmp_path, "slides.pptx", slides_bytes(), {"kind": "slides", "start": 2, "end": 2}
    )
    assert asset.media_type == PPTX and not result["issues"]
    text = "\n".join(item["content"] for item in result["blocks"])
    assert "第二页" in text and "第一页" not in text and '"", "0"' in text and "先解释依据" in text
    assert all(item["source"]["locator"]["slide"] == 2 for item in result["blocks"])
    assert result["coverage"]["processed_ranges"] == [[2, 2]]


def test_pptx_pictures_are_visual_inputs_not_silently_dropped(tmp_path):
    _, result = read_material(tmp_path, "slides.pptx", slides_bytes(include_image=True))
    assert not result["issues"] and len(result["visuals"]) == 1
    assert result["visuals"][0]["locator"]["slide"] == 2
    assert result["visuals"][0]["media_type"] == "image/jpeg"
