"""Document code normalization helpers."""

from __future__ import annotations

import re

from qanorm.utils.text import normalize_whitespace


_DASH_RE = re.compile(r"[‐‑‒–—―]+")
_SPACE_AROUND_DASH_RE = re.compile(r"\s*-\s*")
_SPACE_AROUND_SLASH_RE = re.compile(r"\s*/\s*")
_TRAILING_PUNCT_RE = re.compile(r"[;:,]+$")


def clean_document_code(value: str) -> str:
    """Remove layout noise from a raw document code string."""

    cleaned = value.replace("\xa0", " ")
    cleaned = _DASH_RE.sub("-", cleaned)
    cleaned = normalize_whitespace(cleaned)
    cleaned = _SPACE_AROUND_DASH_RE.sub("-", cleaned)
    cleaned = _SPACE_AROUND_SLASH_RE.sub("/", cleaned)
    cleaned = _TRAILING_PUNCT_RE.sub("", cleaned)
    return cleaned.strip()


def normalize_document_code(value: str) -> str:
    """Convert a document code into a canonical normalized form."""

    cleaned = clean_document_code(value)
    return cleaned.upper()
