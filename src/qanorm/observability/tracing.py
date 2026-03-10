"""Minimal correlation helpers for Stage 1 logging and worker flows."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4


_REQUEST_ID = contextvars.ContextVar("qanorm_request_id", default=None)
_SESSION_ID = contextvars.ContextVar("qanorm_session_id", default=None)
_QUERY_ID = contextvars.ContextVar("qanorm_query_id", default=None)


@dataclass(slots=True, frozen=True)
class CorrelationContext:
    """Current correlation identifiers bound to the running context."""

    request_id: str | None
    session_id: str | None
    query_id: str | None

    def as_log_extra(self) -> dict[str, str]:
        """Return logging fields compatible with structured log sinks."""

        payload: dict[str, str] = {}
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.query_id:
            payload["query_id"] = self.query_id
        return payload


def get_correlation_context() -> CorrelationContext:
    """Return the active request/session/query identifiers."""

    return CorrelationContext(
        request_id=_REQUEST_ID.get(),
        session_id=_SESSION_ID.get(),
        query_id=_QUERY_ID.get(),
    )


@contextlib.contextmanager
def bind_correlation_ids(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    query_id: str | None = None,
) -> Iterator[CorrelationContext]:
    """Temporarily bind correlation identifiers to the current context."""

    request_token = _REQUEST_ID.set(request_id or _REQUEST_ID.get() or uuid4().hex)
    session_token = _SESSION_ID.set(session_id or _SESSION_ID.get())
    query_token = _QUERY_ID.set(query_id or _QUERY_ID.get())
    try:
        yield get_correlation_context()
    finally:
        _REQUEST_ID.reset(request_token)
        _SESSION_ID.reset(session_token)
        _QUERY_ID.reset(query_token)
