from __future__ import annotations

from datetime import date

import pytest

from qanorm.utils.dates import parse_date_string, parse_russian_date_string
from qanorm.utils.retry import build_retry_kwargs
from qanorm.utils.text import normalize_whitespace, strip_html_text, truncate_for_log


def test_normalize_whitespace_collapses_runs_and_trims() -> None:
    assert normalize_whitespace("  one \n\t two   three  ") == "one two three"


def test_strip_html_text_removes_tags_and_normalizes() -> None:
    assert strip_html_text("<div>Hello <b>world</b></div>") == "Hello world"


def test_truncate_for_log_returns_original_when_short() -> None:
    assert truncate_for_log("short text", max_length=20) == "short text"


def test_truncate_for_log_truncates_and_appends_ellipsis() -> None:
    assert truncate_for_log("abcdefghij", max_length=8) == "abcde..."


def test_truncate_for_log_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError):
        truncate_for_log("abc", max_length=0)


def test_parse_date_string_parses_dotted_format() -> None:
    assert parse_date_string("03.03.2026") == date(2026, 3, 3)


def test_parse_russian_date_string_parses_textual_month() -> None:
    assert parse_russian_date_string("1 января 2024") == date(2024, 1, 1)


def test_parse_russian_date_string_rejects_unknown_month() -> None:
    with pytest.raises(ValueError):
        parse_russian_date_string("1 foo 2024")


def test_build_retry_kwargs_returns_tenacity_primitives() -> None:
    retry_kwargs = build_retry_kwargs(max_attempts=4, min_wait_seconds=1.0, max_wait_seconds=5.0)

    assert retry_kwargs["reraise"] is True
    assert retry_kwargs["stop"].max_attempt_number == 4


def test_build_retry_kwargs_rejects_invalid_wait_bounds() -> None:
    with pytest.raises(ValueError):
        build_retry_kwargs(min_wait_seconds=2.0, max_wait_seconds=1.0)


def test_build_retry_kwargs_rejects_empty_retry_types() -> None:
    with pytest.raises(ValueError):
        build_retry_kwargs(retry_on=())
