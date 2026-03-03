"""Date utility helpers."""

from __future__ import annotations

from datetime import date


_RUSSIAN_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def parse_date_string(value: str) -> date:
    """Parse a date in DD.MM.YYYY format."""

    normalized = value.strip()
    return date.fromisoformat("-".join(reversed(normalized.split("."))))


def parse_russian_date_string(value: str) -> date:
    """Parse a Russian textual date like '1 января 2024'."""

    parts = value.strip().lower().split()
    if len(parts) != 3:
        raise ValueError(f"Unsupported Russian date format: {value}")

    day = int(parts[0])
    month = _RUSSIAN_MONTHS.get(parts[1])
    year = int(parts[2])

    if month is None:
        raise ValueError(f"Unsupported Russian month name: {parts[1]}")

    return date(year, month, day)
