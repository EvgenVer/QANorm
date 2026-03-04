"""Node locator helpers."""

from __future__ import annotations

from qanorm.utils.text import normalize_whitespace


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
