"""Background worker implementation."""

from __future__ import annotations

from typing import Any


def run_worker_loop() -> dict[str, Any]:
    """Return a dry-run summary for worker startup."""

    return {
        "status": "ready",
        "message": "Worker entrypoint is ready. Job execution logic will be added in later blocks.",
    }
