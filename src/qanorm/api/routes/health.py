"""Health and readiness endpoints."""

from __future__ import annotations

from uuid import uuid4

from arq import ArqRedis
from fastapi import APIRouter, Depends
from redis import asyncio as redis_asyncio
from sqlalchemy.orm import Session

from qanorm.api.dependencies import check_database_connection, get_arq_redis, get_db_session, get_redis_client, get_runtime_config
from qanorm.settings import RuntimeConfig


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live(settings: RuntimeConfig = Depends(get_runtime_config)) -> dict[str, object]:
    """Return a minimal liveness payload."""

    return {
        "status": "ok",
        "app_env": settings.env.app_env,
    }


@router.get("/ready")
async def ready(
    db: Session = Depends(get_db_session),
    redis: redis_asyncio.Redis = Depends(get_redis_client),
    arq_redis: ArqRedis = Depends(get_arq_redis),
) -> dict[str, object]:
    """Check PostgreSQL, Redis, and ARQ queue publication readiness."""

    db_ready = check_database_connection(db)
    redis_ready = bool(await redis.ping())
    job = await arq_redis.enqueue_job("qa_noop_job", {"kind": "readiness"}, _job_id=f"readiness:{uuid4()}")
    arq_ready = job is not None

    return {
        "status": "ok" if db_ready and redis_ready and arq_ready else "degraded",
        "checks": {
            "database": db_ready,
            "redis": redis_ready,
            "arq_publish": arq_ready,
        },
    }
