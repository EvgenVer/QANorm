"""FastAPI application factory for Stage 2."""

from __future__ import annotations

from fastapi import FastAPI

from qanorm.api.errors import register_error_handlers
from qanorm.api.routes.chat import router as chat_router
from qanorm.api.routes.health import router as health_router
from qanorm.api.routes.metrics import router as metrics_router
from qanorm.api.routes.sessions import router as sessions_router
from qanorm.observability import instrument_fastapi_app


def create_app() -> FastAPI:
    """Build the Stage 2 FastAPI application."""

    app = FastAPI(title="QANorm Stage 2 API", version="0.2.0")
    instrument_fastapi_app(app)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    return app
