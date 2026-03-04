"""Tesseract OCR helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pytesseract


DEFAULT_TESSERACT_LANGUAGES = ("rus", "eng")
DEFAULT_TESSERACT_CONFIG = "--psm 6"


@dataclass(slots=True)
class OcrPageResult:
    """OCR result for one page image."""

    page_number: int
    image_path: Path
    text: str


def run_ocr_for_page(
    image_path: str | Path,
    *,
    page_number: int,
    languages: str | Sequence[str] | None = None,
    config: str = DEFAULT_TESSERACT_CONFIG,
) -> OcrPageResult:
    """Run Tesseract OCR for a single page image."""

    source_path = Path(image_path)
    text = pytesseract.image_to_string(
        str(source_path),
        lang=_normalize_languages(languages),
        config=config,
    )
    return OcrPageResult(
        page_number=page_number,
        image_path=source_path,
        text=_normalize_ocr_text(text),
    )


def run_ocr_for_pages(
    image_paths: Sequence[str | Path],
    *,
    languages: str | Sequence[str] | None = None,
    config: str = DEFAULT_TESSERACT_CONFIG,
) -> list[OcrPageResult]:
    """Run Tesseract OCR for multiple page images in order."""

    return [
        run_ocr_for_page(
            image_path,
            page_number=page_number,
            languages=languages,
            config=config,
        )
        for page_number, image_path in enumerate(image_paths, start=1)
    ]


def merge_ocr_page_texts(page_results: Sequence[OcrPageResult]) -> str:
    """Merge OCR text preserving page order."""

    ordered_pages = sorted(page_results, key=lambda item: item.page_number)
    return "\n\n".join(result.text for result in ordered_pages if result.text)


def _normalize_languages(languages: str | Sequence[str] | None) -> str:
    if languages is None:
        return "+".join(DEFAULT_TESSERACT_LANGUAGES)
    if isinstance(languages, str):
        normalized = languages.strip()
        if not normalized:
            raise ValueError("Tesseract languages string must not be empty")
        return normalized

    normalized_parts = [str(item).strip() for item in languages if str(item).strip()]
    if not normalized_parts:
        raise ValueError("At least one Tesseract language must be provided")
    return "+".join(normalized_parts)


def _normalize_ocr_text(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if " ".join(line.split()))
