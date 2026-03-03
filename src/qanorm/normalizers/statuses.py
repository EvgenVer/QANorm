"""Status normalization helpers."""

from __future__ import annotations

import logging

from qanorm.db.types import StatusNormalized
from qanorm.settings import StatusesConfig, get_settings
from qanorm.utils.text import normalize_whitespace


def _normalize_status_text(value: str | None) -> str:
    """Normalize raw status text for matching."""

    if value is None:
        return ""
    return normalize_whitespace(value).lower()


def get_status_rules(config: StatusesConfig | None = None) -> dict[str, set[str]]:
    """Load normalized active/inactive status rules."""

    status_config = config or get_settings().statuses
    return {
        "active": {_normalize_status_text(item) for item in status_config.active},
        "inactive": {_normalize_status_text(item) for item in status_config.inactive},
    }


def classify_status(value: str | None, config: StatusesConfig | None = None) -> StatusNormalized:
    """Classify a raw status string into the normalized enum."""

    normalized_value = _normalize_status_text(value)
    if not normalized_value:
        return StatusNormalized.UNKNOWN

    rules = get_status_rules(config=config)
    if normalized_value in rules["active"]:
        return StatusNormalized.ACTIVE
    if normalized_value in rules["inactive"]:
        return StatusNormalized.INACTIVE
    return StatusNormalized.UNKNOWN


def is_active_status(value: str | None, config: StatusesConfig | None = None) -> bool:
    """Return True when the status is classified as active."""

    return classify_status(value, config=config) is StatusNormalized.ACTIVE


def is_inactive_status(value: str | None, config: StatusesConfig | None = None) -> bool:
    """Return True when the status is classified as inactive."""

    return classify_status(value, config=config) is StatusNormalized.INACTIVE


def is_unknown_status(value: str | None, config: StatusesConfig | None = None) -> bool:
    """Return True when the status is classified as unknown."""

    return classify_status(value, config=config) is StatusNormalized.UNKNOWN


def resolve_status_conflict(
    list_status: str | None,
    card_status: str | None,
    *,
    logger: logging.Logger | None = None,
    config: StatusesConfig | None = None,
) -> tuple[str | None, StatusNormalized]:
    """Resolve list/card status conflict, preferring the card value when present."""

    normalized_list = _normalize_status_text(list_status)
    normalized_card = _normalize_status_text(card_status)

    if normalized_list and normalized_card and normalized_list != normalized_card:
        (logger or logging.getLogger(__name__)).warning(
            "Status conflict detected between list and card: list=%s card=%s",
            list_status,
            card_status,
        )

    resolved_raw = card_status if normalized_card else list_status
    return resolved_raw, classify_status(resolved_raw, config=config)
