"""Node locator helpers."""

from __future__ import annotations

import re

from qanorm.utils.text import normalize_whitespace

_LOCATOR_PREFIX_RE = re.compile(
    r"^(?:п\.?|пункт|раздел|глава|таблица|приложение|section|chapter|table|appendix)\s+",
    re.IGNORECASE,
)
_LOCATOR_SEPARATORS_RE = re.compile(r"[\s_-]+")


def build_node_locator(
    *,
    node_type: str,
    label: str | None = None,
    order_index: int | None = None,
    parent_locator: str | None = None,
) -> str:
    """Build a deterministic human-readable locator for a structural node."""

    suffix = normalize_whitespace(label or "")
    if not suffix:
        if order_index is None:
            raise ValueError("Either label or order_index must be provided for a node locator")
        suffix = str(order_index)

    current = f"{node_type}:{suffix}"
    if not parent_locator:
        return current
    return f"{parent_locator}/{current}"


def normalize_locator_value(value: str | None) -> str | None:
    """Normalize one human locator string into a lookup-friendly key."""

    if value is None:
        return None

    normalized = normalize_whitespace(value)
    if not normalized:
        return None

    normalized = _LOCATOR_PREFIX_RE.sub("", normalized).strip()
    if not normalized:
        return None

    compact = _LOCATOR_SEPARATORS_RE.sub("", normalized)
    if compact.isdigit():
        return compact
    return normalized.casefold()
