"""Application health checks."""

from __future__ import annotations

from typing import Any

from qanorm.settings import get_settings


def get_health_report() -> dict[str, Any]:
    """Return a minimal health snapshot for the current application setup."""

    settings = get_settings()
    return {
        "status": "ok",
        "app_env": settings.env.app_env,
        "database_url_configured": bool(settings.env.db_url),
        "seed_count": len(settings.sources.seed_urls),
        "raw_storage_path": str(settings.env.raw_storage_path),
    }
