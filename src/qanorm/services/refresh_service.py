"""Document refresh service."""

from __future__ import annotations

from typing import Any


def request_document_refresh(document_code: str) -> dict[str, Any]:
    """Return a dry-run summary for a refresh request."""

    normalized_code = document_code.strip()
    return {
        "status": "queued",
        "message": "Document refresh entrypoint is ready. Refresh implementation will be added in later blocks.",
        "document_code": normalized_code,
    }
