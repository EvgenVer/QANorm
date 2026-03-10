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

from qanorm.agents.planner.query_intent import QueryIntent
from qanorm.db.session import session_scope
from qanorm.db.types import QueryStatus, SearchScope, SearchStatus, SubtaskStatus
from qanorm.models import QAQuery, SearchEvent
from qanorm.repositories import QAEvidenceRepository, QAQueryRepository, QASubtaskRepository, SearchEventRepository
from qanorm.audit import AuditWriter
from qanorm.services.qa.document_resolver import DocumentResolver
from qanorm.services.qa.retrieval_service import (
    RetrievalRequest,
    persist_normative_evidence,
    retrieve_normative_evidence_with_resolution,
)
from qanorm.services.qa.freshness_service import (
    enrich_persisted_answer_with_freshness,
    evaluate_freshness_check,
    queue_refresh_for_freshness_check,
)
from qanorm.services.qa.open_web_service import normalize_open_web_results_to_evidence, search_open_web
from qanorm.services.qa.session_service import SessionService
from qanorm.services.qa.trusted_sources_service import normalize_trusted_hits_to_evidence, search_trusted_sources
from qanorm.services.qa.trusted_sources_service import cleanup_trusted_source_cache, prefetch_trusted_sources
from qanorm.tools import create_tool_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import create_role_bound_providers
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


async def process_query_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one persisted query end-to-end and publish progress events."""

    query_id = UUID(str(payload["query_id"]))
    settings = get_settings()
    redis = ctx["redis"]

    with session_scope() as session:
        from qanorm.agents.answer_synthesizer import AnswerSynthesizer
        from qanorm.agents.orchestrator import QueryOrchestrator

        query_repository = QAQueryRepository(session)
        subtask_repository = QASubtaskRepository(session)
        evidence_repository = QAEvidenceRepository(session)
        search_event_repository = SearchEventRepository(session)
        query = query_repository.get(query_id)
        if query is None:
            raise ValueError(f"Query not found: {query_id}")

        tool_registry = create_tool_registry()
        providers = create_role_bound_providers(
            registry=create_provider_registry(),
            runtime_config=settings,
        )
        orchestrator = QueryOrchestrator.with_redis_progress(
            session,
            tool_registry=tool_registry,
            redis=redis,
            runtime_config=settings,
        )
        synthesizer = AnswerSynthesizer(session, runtime_config=settings, provider=providers.synthesis)

        try:
            planning = await orchestrator.analyze_and_plan(query_id=query_id)
            state = planning.state
            contextual_query_text = state.build_contextual_query_text()
            stored_subtasks = {item.id: item for item in subtask_repository.list_for_query(query_id)}

            for planned_subtask, state_subtask in zip(planning.planned_subtasks, state.subtasks, strict=False):
                if state_subtask.subtask_id is None:
                    continue
                stored_subtask = stored_subtasks.get(state_subtask.subtask_id)
                if stored_subtask is None:
                    continue
                if stored_subtask.status == SubtaskStatus.SKIPPED and planned_subtask.route == "open_web":
                    continue

                stored_subtask.status = SubtaskStatus.RUNNING
                subtask_repository.save(stored_subtask)
                state_subtask.status = SubtaskStatus.RUNNING

                try:
                    if planned_subtask.route == "normative":
                        resolution = DocumentResolver(session).resolve(state)
                        state.document_resolution = resolution.to_payload()
                        query_repository.update_state(
                            query,
                            status=query.status,
                            document_resolution=state.document_resolution,
                        )
                        AuditWriter(session).write(
                            session_id=query.session_id,
                            query_id=query.id,
                            subtask_id=stored_subtask.id,
                            event_type="document_resolution_completed",
                            actor_kind="document_resolver",
                            payload_json=state.document_resolution,
                        )
                        retrieval_result, retrieval_metadata = await retrieve_normative_evidence_with_resolution(
                            session,
                            request=RetrievalRequest(query_text=contextual_query_text, limit=8),
                            resolution=resolution,
                            embedding_provider=providers.embeddings,
                            runtime_config=settings,
                        )
                        search_event_repository.add(
                            SearchEvent(
                                query_id=query.id,
                                subtask_id=stored_subtask.id,
                                provider_name="stage1_normative_corpus",
                                search_scope=SearchScope.NORMATIVE,
                                query_text=contextual_query_text,
                                result_count=len(retrieval_result.all_hits),
                                status=SearchStatus.COMPLETED,
                            )
                        )
                        AuditWriter(session).write(
                            session_id=query.session_id,
                            query_id=query.id,
                            subtask_id=stored_subtask.id,
                            event_type="normative_retrieval_strategy_selected",
                            actor_kind="retrieval_service",
                            payload_json=retrieval_metadata
                            | {
                                "document_resolution": state.document_resolution,
                                "primary_hit_count": len(retrieval_result.primary_hits),
                                "secondary_hit_count": len(retrieval_result.secondary_hits),
                            },
                        )
                        stored_evidence = persist_normative_evidence(
                            session,
                            query_id=query.id,
                            subtask_id=stored_subtask.id,
                            hits=retrieval_result.all_hits,
                        )
                        state.evidence_bundle.normative.extend(stored_evidence)
                        stored_subtask.result_summary = (
                            f"normative_hits={len(stored_evidence)}"
                            f";primary={len(retrieval_result.primary_hits)}"
                            f";secondary={len(retrieval_result.secondary_hits)}"
                            f";scope={retrieval_metadata['final_scope']}"
                            f";fallback={str(retrieval_metadata['fallback_used']).lower()}"
                        )
                    elif planned_subtask.route == "trusted_web":
                        trusted_hits = await search_trusted_sources(
                            session,
                            query_id=query.id,
                            subtask_id=stored_subtask.id,
                            query_text=contextual_query_text,
                            allowed_domains=settings.qa.search.trusted_domains or None,
                            limit=5,
                        )
                        stored_evidence = evidence_repository.add_many(
                            normalize_trusted_hits_to_evidence(
                                query_id=query.id,
                                subtask_id=stored_subtask.id,
                                hits=trusted_hits,
                            )
                        )
                        state.evidence_bundle.trusted_web.extend(stored_evidence)
                        stored_subtask.result_summary = f"trusted_hits={len(stored_evidence)}"
                    elif planned_subtask.route == "open_web":
                        open_web_results = await search_open_web(
                            session,
                            query_id=query.id,
                            subtask_id=stored_subtask.id,
                            query_text=contextual_query_text,
                            limit=settings.qa.search.open_web_max_results,
                        )
                        stored_evidence = evidence_repository.add_many(
                            normalize_open_web_results_to_evidence(
                                query_id=query.id,
                                subtask_id=stored_subtask.id,
                                results=open_web_results,
                            )
                        )
                        state.evidence_bundle.open_web.extend(stored_evidence)
                        stored_subtask.result_summary = f"open_web_hits={len(stored_evidence)}"
                    elif planned_subtask.route == "freshness":
                        checks = await orchestrator.schedule_freshness_branch(
                            query_id=query.id,
                            evidence_rows=state.evidence_bundle.normative,
                            scheduler=None,
                        )
                        stored_subtask.result_summary = f"freshness_checks={len(checks)}"
                    else:
                        stored_subtask.result_summary = "unsupported_route"
                    stored_subtask.status = SubtaskStatus.COMPLETED
                    state_subtask.status = SubtaskStatus.COMPLETED
                except Exception as subtask_error:
                    # External-source failures should degrade the answer, not leave the
                    # whole query hanging in pending state.
                    stored_subtask.status = SubtaskStatus.FAILED
                    stored_subtask.result_summary = f"{planned_subtask.route}_failed:{str(subtask_error)[:240]}"
                    state_subtask.status = SubtaskStatus.FAILED

                subtask_repository.save(stored_subtask)
                state_subtask.result_summary = stored_subtask.result_summary

            query_repository.update_state(
                query,
                status=QueryStatus.SYNTHESIZING,
                requires_freshness_check=state.requires_freshness_check,
                used_open_web=bool(state.evidence_bundle.open_web),
                used_trusted_web=bool(state.evidence_bundle.trusted_web),
            )
            await publish_progress_event(redis, query_id=query.id, event="partial_answer", data={"partial_markdown": "Synthesizing answer..."})

            limitations: list[str] = []
            if not state.evidence_bundle.all_items:
                limitations.append("No evidence was found for this query in the currently available sources.")
            if state.intent == QueryIntent.NO_RETRIEVAL.value:
                limitations.append("The request was intentionally stopped before retrieval because it is outside the normative retrieval path.")
            answer = await synthesizer.synthesize(state, limitations=limitations)
            synthesizer.persist_answer(query=query, answer=answer)
            await publish_progress_event(
                redis,
                query_id=query.id,
                event="answer_completed",
                data={"partial_markdown": answer.markdown, "coverage_status": answer.coverage_status.value},
            )
            return {
                "status": "ok",
                "query_id": str(query.id),
                "evidence_count": len(state.evidence_bundle.all_items),
                "coverage_status": answer.coverage_status.value,
            }
        except Exception as exc:
            query_repository.update_state(
                query,
                status=QueryStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
            )
            await publish_progress_event(
                redis,
                query_id=query.id,
                event="query_failed",
                data={"error": str(exc)[:500]},
            )
            session.commit()
            return {
                "status": "failed",
                "query_id": str(query.id),
                "error": str(exc)[:500],
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


async def post_answer_enrichment_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Apply freshness warnings to the persisted answer after background checks finish."""

    with session_scope() as session:
        return enrich_persisted_answer_with_freshness(
            session,
            query_id=UUID(str(payload["query_id"])),
        )


async def trusted_source_cache_cleanup_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Delete expired trusted-source cache rows."""

    with session_scope() as session:
        deleted = cleanup_trusted_source_cache(session)
    return {"status": "ok", "deleted_entries": deleted}


async def trusted_source_prefetch_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Warm trusted-source cache for one query and source subset."""

    query_text = str(payload["query_text"]).strip()
    if not query_text:
        raise ValueError("'query_text' is required for trusted_source_prefetch_job")
    allowed_domains = [str(item).strip() for item in payload.get("allowed_domains", []) if str(item).strip()]
    limit = int(payload.get("limit", 5))
    with session_scope() as session:
        result = await prefetch_trusted_sources(
            session,
            query_text=query_text,
            allowed_domains=allowed_domains,
            limit=limit,
        )
    return {
        "status": "ok",
        "source_count": result.source_count,
        "hit_count": result.hit_count,
        "cache_hit_count": result.cache_hit_count,
    }


async def open_web_research_job(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Run one open-web search request and normalize the fetched evidence payload."""

    query_id = UUID(str(payload["query_id"])) if payload.get("query_id") else None
    subtask_id = UUID(str(payload["subtask_id"])) if payload.get("subtask_id") else None
    query_text = str(payload["query_text"])
    limit = int(payload.get("limit", get_settings().qa.search.open_web_max_results))
    allowed_domains = [str(item).strip() for item in payload.get("allowed_domains", []) if str(item).strip()]
    with session_scope() as session:
        results = await search_open_web(
            session,
            query_id=query_id,
            subtask_id=subtask_id,
            query_text=query_text,
            allowed_domains=allowed_domains,
            limit=limit,
        )
        evidence = (
            normalize_open_web_results_to_evidence(
                query_id=query_id,
                subtask_id=subtask_id,
                results=results,
            )
            if query_id is not None
            else []
        )
    return {
        "query_text": query_text,
        "result_count": len(results),
        "results": [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "engine": item.engine,
                "score": item.score,
            }
            for item in results
        ],
        "evidence_count": len(evidence),
    }


class Stage2WorkerSettings:
    """ARQ worker settings for the Stage 2 runtime."""

    redis_settings = build_redis_settings()
    functions = [
        qa_noop_job,
        process_query_job,
        cleanup_session_state_job,
        cleanup_expired_sessions_job,
        freshness_check_job,
        document_refresh_job,
        post_answer_enrichment_job,
        trusted_source_cache_cleanup_job,
        trusted_source_prefetch_job,
        open_web_research_job,
    ]
    queue_name = "arq:queue"
    max_jobs = 10
