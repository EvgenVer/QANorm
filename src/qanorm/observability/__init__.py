"""Observability helpers for metrics, traces, and structured logs."""

from qanorm.observability.metrics import (
    export_metrics,
    increment_event,
    observe_query_latency,
    set_backfill_metric,
    set_retrieval_metric,
    set_verification_metric,
)
from qanorm.observability.tracing import (
    CorrelationContext,
    REQUEST_ID_HEADER,
    bind_correlation_ids,
    get_correlation_context,
    instrument_arq_worker,
    instrument_fastapi_app,
    record_provider_trace,
    record_tool_trace,
    set_query_id,
    set_session_id,
    trace_span,
)

__all__ = [
    "CorrelationContext",
    "REQUEST_ID_HEADER",
    "bind_correlation_ids",
    "export_metrics",
    "get_correlation_context",
    "increment_event",
    "instrument_arq_worker",
    "instrument_fastapi_app",
    "observe_query_latency",
    "record_provider_trace",
    "record_tool_trace",
    "set_backfill_metric",
    "set_query_id",
    "set_retrieval_metric",
    "set_session_id",
    "set_verification_metric",
    "trace_span",
]
