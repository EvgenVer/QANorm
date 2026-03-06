"""Dependency providers for FastAPI routes."""

from __future__ import annotations

from collections.abc import Generator

from arq import ArqRedis
from redis import asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.orm import Session

from qanorm.db.session import create_session_factory
from qanorm.settings import RuntimeConfig, get_settings
from qanorm.workers.stage2 import create_arq_pool, create_redis_client


def get_runtime_config() -> RuntimeConfig:
    """Return the normalized runtime configuration bundle."""

    return get_settings()


def get_db_session() -> Generator[Session, None, None]:
    """Yield one SQLAlchemy session per request."""

    session = create_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_redis_client() -> Generator[redis_asyncio.Redis, None, None]:
    """Yield one asyncio Redis client per request."""

    redis = create_redis_client()
    try:
        yield redis
    finally:
        await redis.aclose()


async def get_arq_redis() -> Generator[ArqRedis, None, None]:
    """Yield one ARQ Redis pool per request."""

    arq_redis = await create_arq_pool()
    try:
        yield arq_redis
    finally:
        await arq_redis.aclose()


def check_database_connection(db: Session) -> bool:
    """Run a lightweight query against PostgreSQL."""

    db.execute(text("SELECT 1"))
    return True
