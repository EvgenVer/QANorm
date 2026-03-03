"""Document indexing workflow."""

from __future__ import annotations

from typing import Any


def reindex(document_code: str | None = None) -> dict[str, Any]:
    """Return a dry-run summary for reindex requests."""

    scope = "single-document" if document_code else "all-documents"
    return {
        "status": "queued",
        "message": "Reindex entrypoint is ready. Indexing implementation will be expanded in later blocks.",
        "scope": scope,
        "document_code": document_code,
    }
