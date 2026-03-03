"""Document processing pipeline service."""

from __future__ import annotations

from typing import Any


def get_pipeline_status() -> dict[str, Any]:
    """Return a minimal pipeline status snapshot."""

    return {
        "status": "ready",
        "message": "Document pipeline shell is available.",
    }
