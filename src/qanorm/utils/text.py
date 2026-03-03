"""Text utility helpers."""

from __future__ import annotations

import re


_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def normalize_whitespace(value: str) -> str:
    """Collapse repeated whitespace and trim outer spaces."""

    return _WHITESPACE_RE.sub(" ", value).strip()


def strip_html_text(value: str) -> str:
    """Remove simple HTML tags and normalize the remaining text."""

    without_tags = _HTML_TAG_RE.sub(" ", value)
    return normalize_whitespace(without_tags)


def truncate_for_log(value: str, max_length: int = 160) -> str:
    """Safely truncate text for logs while preserving readability."""

    normalized = normalize_whitespace(value)
    if max_length <= 0:
        raise ValueError("max_length must be greater than zero")
    if len(normalized) <= max_length:
        return normalized
    if max_length <= 3:
        return normalized[:max_length]
    return f"{normalized[: max_length - 3]}..."
