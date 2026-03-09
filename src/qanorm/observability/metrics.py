"""Lightweight metrics registry with optional Prometheus integration."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from threading import Lock

try:
    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
except ImportError:  # pragma: no cover - optional dependency
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    CollectorRegistry = None
    Counter = None
    Gauge = None
    Histogram = None
    generate_latest = None


class _FallbackMetricsRegistry:
    """Small in-memory registry used when prometheus_client is unavailable."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        """Increment one fallback counter."""

        with self._lock:
            self._counters[(name, _freeze_labels(labels))] += value

    def set(self, name: str, value: float, **labels: str) -> None:
        """Set one fallback gauge value."""

        with self._lock:
            self._gauges[(name, _freeze_labels(labels))] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Append one observation into a fallback histogram."""

        with self._lock:
            self._histograms[(name, _freeze_labels(labels))].append(value)

    def render(self) -> bytes:
        """Expose all fallback metrics in Prometheus text format."""

        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(_render_metric_line(name, value, labels))
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(_render_metric_line(name, value, labels))
            for (name, labels), values in sorted(self._histograms.items()):
                count = len(values)
                total = sum(values)
                lines.append(_render_metric_line(f"{name}_count", count, labels))
                lines.append(_render_metric_line(f"{name}_sum", total, labels))
        return ("\n".join(lines) + "\n").encode("utf-8")


def _freeze_labels(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Normalize label pairs into a stable hashable tuple."""

    return tuple(sorted((key, str(value)) for key, value in labels.items()))


def _render_metric_line(name: str, value: float, labels: tuple[tuple[str, str], ...]) -> str:
    """Render one Prometheus-compatible metric line."""

    if not labels:
        return f"{name} {value}"
    rendered_labels = ",".join(f'{key}="{value}"' for key, value in labels)
    return f"{name}{{{rendered_labels}}} {value}"


if CollectorRegistry is not None:
    REGISTRY = CollectorRegistry()
    QUERY_LATENCY = Histogram(
        "qanorm_query_stage_seconds",
        "Observed stage durations for Stage 2 queries.",
        ("metric",),
        registry=REGISTRY,
    )
    EVENT_COUNTER = Counter(
        "qanorm_events_total",
        "Count of tracked Stage 2 runtime events.",
        ("kind", "status"),
        registry=REGISTRY,
    )
    BACKFILL_GAUGE = Gauge(
        "qanorm_dense_backfill_progress",
        "Dense embedding backfill progress for retrieval chunks.",
        ("metric",),
        registry=REGISTRY,
    )
    RETRIEVAL_GAUGE = Gauge(
        "qanorm_retrieval_metrics",
        "Retrieval metrics, including estimate deltas and chunk coverage.",
        ("metric",),
        registry=REGISTRY,
    )
    VERIFICATION_GAUGE = Gauge(
        "qanorm_verification_metrics",
        "Verification and freshness quality metrics.",
        ("metric",),
        registry=REGISTRY,
    )
else:  # pragma: no cover - exercised indirectly through export_metrics
    REGISTRY = _FallbackMetricsRegistry()
    QUERY_LATENCY = None
    EVENT_COUNTER = None
    BACKFILL_GAUGE = None
    RETRIEVAL_GAUGE = None
    VERIFICATION_GAUGE = None


def observe_query_latency(metric: str, value_seconds: float) -> None:
    """Record a query latency metric."""

    if QUERY_LATENCY is not None:
        QUERY_LATENCY.labels(metric=metric).observe(value_seconds)
    else:
        REGISTRY.observe("qanorm_query_stage_seconds", value_seconds, metric=metric)


def increment_event(kind: str, status: str = "ok", value: float = 1.0) -> None:
    """Increment one event counter."""

    if EVENT_COUNTER is not None:
        EVENT_COUNTER.labels(kind=kind, status=status).inc(value)
    else:
        REGISTRY.inc("qanorm_events_total", value, kind=kind, status=status)


def set_backfill_metric(metric: str, value: float) -> None:
    """Expose one dense-backfill progress gauge."""

    if BACKFILL_GAUGE is not None:
        BACKFILL_GAUGE.labels(metric=metric).set(value)
    else:
        REGISTRY.set("qanorm_dense_backfill_progress", value, metric=metric)


def set_retrieval_metric(metric: str, value: float) -> None:
    """Expose one retrieval quality or coverage gauge."""

    if RETRIEVAL_GAUGE is not None:
        RETRIEVAL_GAUGE.labels(metric=metric).set(value)
    else:
        REGISTRY.set("qanorm_retrieval_metrics", value, metric=metric)


def set_verification_metric(metric: str, value: float) -> None:
    """Expose one verification or freshness gauge."""

    if VERIFICATION_GAUGE is not None:
        VERIFICATION_GAUGE.labels(metric=metric).set(value)
    else:
        REGISTRY.set("qanorm_verification_metrics", value, metric=metric)


def export_metrics() -> tuple[bytes, str]:
    """Render the active metrics registry into a response payload."""

    if generate_latest is not None:
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
    return REGISTRY.render(), CONTENT_TYPE_LATEST
