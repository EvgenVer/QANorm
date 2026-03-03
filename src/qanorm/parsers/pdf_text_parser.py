"""PDF text extraction parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from qanorm.ocr.quality import calculate_pdf_text_layer_score, should_run_ocr_for_pdf


@dataclass(slots=True)
class PdfTextExtractionResult:
    """Text extracted from a PDF, both per-page and aggregated."""

    page_texts: list[str]
    combined_text: str
    text_layer_score: float
    needs_ocr: bool


def extract_text_from_pdf(path: str | Path) -> PdfTextExtractionResult:
    """Extract text from a PDF file using PyMuPDF."""

    document = fitz.open(path)
    try:
        page_texts = [_normalize_page_text(page.get_text("text")) for page in document]
    finally:
        document.close()

    combined_text = "\n\n".join(page_text for page_text in page_texts if page_text)
    text_layer_score = calculate_pdf_text_layer_score(page_texts)
    needs_ocr = should_run_ocr_for_pdf(page_texts)
    return PdfTextExtractionResult(
        page_texts=page_texts,
        combined_text=combined_text,
        text_layer_score=text_layer_score,
        needs_ocr=needs_ocr,
    )


def _normalize_page_text(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if " ".join(line.split()))
