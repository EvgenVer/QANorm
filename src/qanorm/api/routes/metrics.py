"""Metrics export endpoint for Prometheus-compatible scraping."""

from __future__ import annotations

from fastapi import APIRouter, Response

from qanorm.observability import export_metrics


router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def export_metrics_endpoint() -> Response:
    """Expose the process metrics registry in Prometheus text format."""

    payload, media_type = export_metrics()
    return Response(content=payload, media_type=media_type)
