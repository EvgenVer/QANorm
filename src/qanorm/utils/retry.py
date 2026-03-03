"""Retry utility helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential


def build_retry_kwargs(
    *,
    max_attempts: int = 3,
    min_wait_seconds: float = 1.0,
    max_wait_seconds: float = 10.0,
    retry_on: Iterable[type[BaseException]] = (Exception,),
) -> dict[str, Any]:
    """Build a reusable tenacity retry configuration."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if min_wait_seconds <= 0:
        raise ValueError("min_wait_seconds must be greater than 0")
    if max_wait_seconds < min_wait_seconds:
        raise ValueError("max_wait_seconds must be greater than or equal to min_wait_seconds")

    retry_types = tuple(retry_on)
    if not retry_types:
        raise ValueError("retry_on must contain at least one exception type")

    return {
        "stop": stop_after_attempt(max_attempts),
        "wait": wait_exponential(multiplier=min_wait_seconds, min=min_wait_seconds, max=max_wait_seconds),
        "retry": retry_if_exception_type(retry_types),
        "reraise": True,
    }
