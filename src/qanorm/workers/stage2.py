"""Stage 2 Redis, ARQ, and streaming runtime helpers."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis import asyncio as redis_asyncio

from qanorm.db.session import session_scope
from qanorm.services.qa.freshness_service import evaluate_freshness_check, queue_refresh_for_freshness_check
from qanorm.services.qa.session_service import SessionService
from qanorm.settings import get_qa_config, get_settings


REDIS_NAMESPACE = "qanorm:qa"


@dataclass(slots=True)
class Stage2ProgressEvent:
    """Serializable event payload shared between ARQ, Redis, and SSE."""

    event: str
    query_id: str
    data: dict[str, Any]
    created_at: str


class SessionLockError(RuntimeError):
    """Raised when a per-session lock cannot be acquired."""


def build_session_namespace(session_id: UUID | str) -> str:
    """Return the namespaced Redis prefix for one session."""

    return f"{REDIS_NAMESPACE}:session:{session_id}"


def build_session_lock_key(session_id: UUID | str) -> str:
    """Return the lock key for one session."""

    return f"{build_session_namespace(session_id)}:lock"


def build_query_events_channel(query_id: UUID | str) -> str:
    """Return the pubsub channel used for query progress events."""

    return f"{REDIS_NAMESPACE}:query:{query_id}:events"


def build_redis_settings(redis_url: str | None = None) -> RedisSettings:
    """Translate the configured Redis URL into ARQ Redis settings."""

    parsed = urlparse(redis_url or get_settings().env.redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "0"),
        username=parsed.username,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )


def create_redis_client(redis_url: str | None = None) -> redis_asyncio.Redis:
    """Create a shared asyncio Redis client."""

    return redis_asyncio.from_url(
        redis_url or get_settings().env.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def create_arq_pool(redis_url: str | None = None) -> ArqRedis:
    """Create an ARQ pool against the configured Redis instance."""

    return await create_pool(build_redis_settings(redis_url))


@asynccontextmanager
async def session_lock(
    redis: redis_asyncio.Redis,
    session_id: UUID | str,
    *,
    ttl_seconds: int = 60,
) -> Any:
    """Acquire and release a per-session Redis lock."""

    lock_key = build_session_lock_key(session_id)
    token = uuid4().hex
    acquired = await redis.set(lock_key, token, ex=ttl_seconds, nx=True)
    if not acquired:
        raise SessionLockError(f"Session {session_id} is already locked.")

    try:
        yield lock_key
    finally:
        if await redis.get(lock_key) == token:
            await redis.delete(lock_key)


async def publish_progress_event(
    redis: redis_asyncio.Redis,
    *,
    query_id: UUID | str,
    event: str,
    data: dict[str, Any] | None = None,
) -> Stage2ProgressEvent:
    """Publish one structured progress event into Redis pubsub."""

    payload = Stage2ProgressEvent(
        event=event,
        query_id=str(query_id),
        data=data or {},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await redis.publish(build_query_events_channel(query_id), json.dumps(asdict(payload), ensure_ascii=False))
    return payload


async def qa_noop_job(ctx: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Minimal ARQ job used by readiness checks and smoke tests."""

    return {
        "status": "ok",
        "kind": (payload or {}).get("kind", "noop"),
    }


async def cleanup_session_state_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Remove expired hot-state keys from Redis."""

    redis = ctx["redis"]
    deleted = 0
    async for key in redis.scan_iter(match=f"{REDIS_NAMESPACE}:session:*"):
        ttl = await redis.ttl(key)
        if ttl == -1:
            continue
        if ttl <= 0:
            deleted += await redis.delete(key)
    return {"status": "ok", "deleted_keys": deleted}


async def cleanup_expired_sessions_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Delete expired session roots and rely on FK cascades for child data."""

    with session_scope() as session:
        removed = SessionService(session, qa_config=get_qa_config()).cleanup_expired_sessions()
    return {"status": "ok", "removed_sessions": removed}


async def freshness_check_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one pending freshness check and optionally queue a refresh."""

    with session_scope() as session:
        result = evaluate_freshness_check(
            session,
            freshness_check_id=UUID(str(payload["freshness_check_id"])),
            auto_queue_refresh=bool(payload.get("auto_queue_refresh", True)),
        )
    return result.to_payload()


async def document_refresh_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Queue or reuse a Stage 1 refresh job for one persisted freshness check."""

    with session_scope() as session:
        result = queue_refresh_for_freshness_check(
            session,
            freshness_check_id=UUID(str(payload["freshness_check_id"])),
        )
    return result.to_payload()


class Stage2WorkerSettings:
    """ARQ worker settings for the Stage 2 runtime."""

    redis_settings = build_redis_settings()
    functions = [
        qa_noop_job,
        cleanup_session_state_job,
        cleanup_expired_sessions_job,
        freshness_check_job,
        document_refresh_job,
    ]
    queue_name = "arq:queue"
    max_jobs = 10
