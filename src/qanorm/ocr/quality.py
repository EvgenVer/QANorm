"""OCR quality scoring helpers."""

from __future__ import annotations


def calculate_pdf_text_layer_score(page_texts: list[str]) -> float:
    """Estimate PDF text-layer quality from extracted page text."""

    if not page_texts:
        return 0.0

    non_empty_pages = [page for page in page_texts if page.strip()]
    if not non_empty_pages:
        return 0.0

    average_chars = sum(len(page) for page in non_empty_pages) / len(page_texts)
    if average_chars >= 200:
        return 1.0
    if average_chars <= 5:
        return max(0.0, average_chars / 50.0)
    return max(0.2, min(1.0, average_chars / 200.0))


def should_run_ocr_for_pdf(page_texts: list[str]) -> bool:
    """Return ``True`` when the PDF text layer is too weak and OCR should run."""

    return calculate_pdf_text_layer_score(page_texts) < 0.2
