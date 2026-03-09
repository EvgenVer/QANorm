from __future__ import annotations

from qanorm.observability import (
    bind_correlation_ids,
    export_metrics,
    get_correlation_context,
    increment_event,
    observe_query_latency,
    set_backfill_metric,
)


def test_bind_correlation_ids_sets_runtime_context() -> None:
    with bind_correlation_ids(request_id="req-1", session_id="sess-1", query_id="query-1"):
        context = get_correlation_context()
        assert context.request_id == "req-1"
        assert context.session_id == "sess-1"
        assert context.query_id == "query-1"


def test_export_metrics_returns_prometheus_text() -> None:
    increment_event("query_created", status="ok")
    observe_query_latency("time_to_final_answer", 1.25)
    set_backfill_metric("generated_embedding_count", 42)

    payload, media_type = export_metrics()

    assert media_type.startswith("text/plain")
    text = payload.decode("utf-8")
    assert "qanorm_events_total" in text
