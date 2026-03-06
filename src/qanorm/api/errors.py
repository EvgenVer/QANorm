"""Shared API error types and handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class APIError(RuntimeError):
    """Typed API error converted into a structured JSON response."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    """Render ``APIError`` as the unified API error contract."""

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach API error handlers to the FastAPI app."""

    app.add_exception_handler(APIError, api_error_handler)
