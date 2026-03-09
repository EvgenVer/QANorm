"""Correlation IDs, tracing hooks, and FastAPI instrumentation helpers."""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from qanorm.observability.metrics import increment_event, observe_query_latency

try:  # pragma: no cover - optional dependency
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError:  # pragma: no cover - optional dependency
    trace = None
    FastAPIInstrumentor = None


LOGGER = logging.getLogger("qanorm.observability")
REQUEST_ID_HEADER = "X-Request-ID"

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
        """Return logging fields compatible with JSON and Loki log sinks."""

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


def set_query_id(query_id: str | None) -> None:
    """Bind the query id for downstream provider and tool traces."""

    _QUERY_ID.set(query_id)


def set_session_id(session_id: str | None) -> None:
    """Bind the session id for downstream provider and tool traces."""

    _SESSION_ID.set(session_id)


@contextlib.contextmanager
def trace_span(name: str, *, attributes: dict[str, str | int | float | bool] | None = None) -> Iterator[None]:
    """Create an OTel span when available and otherwise behave as a no-op."""

    if trace is None:  # pragma: no branch - fast no-op path
        yield
        return

    tracer = trace.get_tracer("qanorm.stage2")
    with tracer.start_as_current_span(name) as span:
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        yield


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach correlation ids to every HTTP request and response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER, uuid4().hex)
        session_id = request.path_params.get("session_id") if hasattr(request, "path_params") else None
        query_id = request.path_params.get("query_id") if hasattr(request, "path_params") else None
        started_at = perf_counter()
        with bind_correlation_ids(
            request_id=request_id,
            session_id=str(session_id) if session_id else None,
            query_id=str(query_id) if query_id else None,
        ) as context:
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = context.request_id or request_id
        observe_query_latency("http_request", perf_counter() - started_at)
        increment_event("http_request", status=str(response.status_code))
        return response


def instrument_fastapi_app(app) -> None:
    """Attach FastAPI middleware and optional OpenTelemetry instrumentation."""

    app.add_middleware(CorrelationIdMiddleware)
    if FastAPIInstrumentor is not None:  # pragma: no branch - optional dependency
        FastAPIInstrumentor.instrument_app(app)


def instrument_arq_worker(*, worker_name: str) -> None:
    """Record worker bootstrap instrumentation when optional tracing is available."""

    with trace_span("worker.bootstrap", attributes={"worker.name": worker_name}):
        increment_event("worker_bootstrap", status="ok")


def record_provider_trace(
    *,
    provider_name: str,
    model_name: str,
    operation: str,
    duration_seconds: float,
    status: str,
) -> None:
    """Persist provider-call metrics and emit structured logs."""

    with trace_span(
        "provider.call",
        attributes={
            "provider.name": provider_name,
            "provider.model": model_name,
            "provider.operation": operation,
            "provider.status": status,
        },
    ):
        observe_query_latency(f"provider_{operation}", duration_seconds)
        increment_event("provider_call", status=status)
        LOGGER.info(
            "provider_call",
            extra={
                **get_correlation_context().as_log_extra(),
                "provider_name": provider_name,
                "model_name": model_name,
                "operation": operation,
                "status": status,
                "duration_seconds": round(duration_seconds, 6),
            },
        )


def record_tool_trace(*, tool_name: str, scope: str, duration_seconds: float, status: str) -> None:
    """Persist tool-call metrics and structured logs."""

    with trace_span(
        "tool.call",
        attributes={
            "tool.name": tool_name,
            "tool.scope": scope,
            "tool.status": status,
        },
    ):
        observe_query_latency("tool_call", duration_seconds)
        increment_event("tool_call", status=status)
        LOGGER.info(
            "tool_call",
            extra={
                **get_correlation_context().as_log_extra(),
                "tool_name": tool_name,
                "tool_scope": scope,
                "status": status,
                "duration_seconds": round(duration_seconds, 6),
            },
        )
