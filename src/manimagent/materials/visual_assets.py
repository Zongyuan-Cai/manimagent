"""Bounded raster preparation inside the disposable parser process."""

from __future__ import annotations

import base64
import io
import math

from manimagent.errors import ContentError

MAX_PIXELS = 25_000_000
MAX_EDGE = 2000
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def image_visual(raw: bytes, locator: dict, label: str) -> dict:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ContentError(
                "budget_exceeded", f"{label}像素过大，请缩小或拆分图片", state="needs_input"
            )
        if getattr(source, "n_frames", 1) != 1:
            raise ContentError(
                "content_uncertain", f"{label}含多帧，请按页拆成静态图片", state="needs_review"
            )
        source.load()
        prepared = ImageOps.exif_transpose(source).convert("RGBA")
        canvas = Image.new("RGB", prepared.size, "white")
        canvas.paste(prepared, mask=prepared.getchannel("A"))
        canvas.thumbnail((MAX_EDGE, MAX_EDGE))
        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=92)
    data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ContentError(
            "budget_exceeded", f"{label}转换后仍过大，请拆分图片", state="needs_input"
        )
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "media_type": "image/jpeg",
        "locator": locator,
        "label": label,
        "width": canvas.width,
        "height": canvas.height,
    }


def pdf_visual(path, page_number: int) -> dict:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        page = document[page_number - 1]
        width, height = page.get_size()
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ContentError("invalid_file", "PDF 页面尺寸无效", state="needs_input")
        scale = min(2, MAX_EDGE / max(width, height))
        bitmap = page.render(scale=scale)
        try:
            output = io.BytesIO()
            bitmap.to_pil().convert("RGB").save(output, format="JPEG", quality=92)
            return image_visual(output.getvalue(), {"page": page_number}, f"第 {page_number} 页")
        finally:
            bitmap.close()
            page.close()
