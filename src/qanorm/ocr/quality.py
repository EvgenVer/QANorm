"""OCR quality scoring helpers."""

from __future__ import annotations

from qanorm.settings import get_app_config


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


def calculate_ocr_confidence(page_texts: list[str]) -> float:
    """Estimate OCR confidence from page text density and character quality."""

    if not page_texts:
        return 0.0

    combined_text = "\n".join(page_texts)
    if not combined_text.strip():
        return 0.0

    meaningful_chars = sum(
        1
        for char in combined_text
        if char.isalnum() or char in " .,;:!?()[]{}%/\\-+*\"'№"
    )
    non_whitespace_chars = sum(1 for char in combined_text if not char.isspace())
    if non_whitespace_chars == 0:
        return 0.0

    useful_ratio = meaningful_chars / max(1, non_whitespace_chars)
    average_density = non_whitespace_chars / max(1, len(page_texts))
    density_score = min(1.0, average_density / 300.0)
    confidence = (useful_ratio * 0.65) + (density_score * 0.35)
    return max(0.0, min(1.0, round(confidence, 4)))


def get_low_confidence_threshold(threshold: float | None = None) -> float:
    """Return the explicit or configured OCR low-confidence threshold."""

    resolved_threshold = threshold
    if resolved_threshold is None:
        resolved_threshold = get_app_config().ocr_low_confidence_threshold
    if not 0.0 <= resolved_threshold <= 1.0:
        raise ValueError("Low-confidence OCR threshold must be between 0.0 and 1.0")
    return resolved_threshold


def is_low_confidence_parse(score: float, *, threshold: float | None = None) -> bool:
    """Return ``True`` when OCR confidence falls below the configured threshold."""

    return score < get_low_confidence_threshold(threshold)
