"""PDF page rendering for OCR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from qanorm.settings import get_app_config


@dataclass(slots=True)
class RenderedPdfPage:
    """A rendered PDF page image prepared for OCR."""

    page_number: int
    dpi: int
    image_path: Path


def get_ocr_render_dpi(dpi: int | None = None) -> int:
    """Return the explicit or configured DPI used for OCR rendering."""

    resolved_dpi = dpi if dpi is not None else get_app_config().ocr_render_dpi
    if resolved_dpi <= 0:
        raise ValueError("OCR render DPI must be a positive integer")
    return resolved_dpi


def render_pdf_pages(
    path: str | Path,
    *,
    output_dir: str | Path,
    dpi: int | None = None,
) -> list[RenderedPdfPage]:
    """Render PDF pages to PNG files ready for OCR."""

    source_path = Path(path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    resolved_dpi = get_ocr_render_dpi(dpi)
    scale = resolved_dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    document = fitz.open(source_path)
    try:
        rendered_pages: list[RenderedPdfPage] = []
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = target_dir / f"page_{page_number:04d}.png"
            pixmap.save(image_path)
            rendered_pages.append(
                RenderedPdfPage(
                    page_number=page_number,
                    dpi=resolved_dpi,
                    image_path=image_path,
                )
            )
        return rendered_pages
    finally:
        document.close()
