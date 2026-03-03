from __future__ import annotations

import logging

from qanorm.db.types import StatusNormalized
from qanorm.normalizers.codes import clean_document_code, normalize_document_code
from qanorm.normalizers.statuses import (
    classify_status,
    get_status_rules,
    is_active_status,
    is_inactive_status,
    is_unknown_status,
    resolve_status_conflict,
)
from qanorm.settings import StatusesConfig, get_settings


def _status_config() -> StatusesConfig:
    return StatusesConfig(
        active=["действует", "действующий", "взамен"],
        inactive=[
            "утратил силу",
            "не действует",
            "заменен",
            "отменен",
        ],
    )


def test_get_status_rules_builds_normalized_sets() -> None:
    rules = get_status_rules(config=_status_config())

    assert "действует" in rules["active"]
    assert "утратил силу" in rules["inactive"]


def test_get_status_rules_loads_from_application_settings(monkeypatch) -> None:
    monkeypatch.setenv("QANORM_DB_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/qanorm")
    get_settings.cache_clear()
    try:
        rules = get_status_rules()
    finally:
        get_settings.cache_clear()

    assert "взамен" in rules["active"]


def test_classify_status_recognizes_active_inactive_and_unknown() -> None:
    config = _status_config()

    assert classify_status("  Действует  ", config=config) is StatusNormalized.ACTIVE
    assert classify_status("утратил силу", config=config) is StatusNormalized.INACTIVE
    assert classify_status("что-то иное", config=config) is StatusNormalized.UNKNOWN


def test_status_predicates_delegate_to_classification() -> None:
    config = _status_config()

    assert is_active_status("взамен", config=config) is True
    assert is_inactive_status("заменен", config=config) is True
    assert is_unknown_status("архив", config=config) is True


def test_resolve_status_conflict_prefers_card_and_logs_warning(caplog) -> None:
    caplog.set_level(logging.WARNING)

    resolved_raw, resolved_status = resolve_status_conflict(
        "действует",
        "утратил силу",
        config=_status_config(),
    )

    assert resolved_raw == "утратил силу"
    assert resolved_status is StatusNormalized.INACTIVE
    assert "Status conflict detected" in caplog.text


def test_resolve_status_conflict_uses_list_value_when_card_missing() -> None:
    resolved_raw, resolved_status = resolve_status_conflict(
        "взамен",
        None,
        config=_status_config(),
    )

    assert resolved_raw == "взамен"
    assert resolved_status is StatusNormalized.ACTIVE


def test_clean_document_code_removes_noise_and_normalizes_spacing() -> None:
    assert clean_document_code(" ГОСТ\xa0Р 2.001 - 2023; ") == "ГОСТ Р 2.001-2023"


def test_normalize_document_code_uppercases_result() -> None:
    assert normalize_document_code("sp 20.13330 / 2016") == "SP 20.13330/2016"
