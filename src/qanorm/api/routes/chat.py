"""Chat query endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from redis import asyncio as redis_asyncio
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from qanorm.api.dependencies import get_db_session, get_redis_client
from qanorm.api.errors import APIError
from qanorm.api.schemas import (
    AnswerCitationResponse,
    AnswerResponse,
    AnswerSectionResponse,
    EvidenceResponse,
    MessageRequest,
    MessageResponse,
    QueryDetailResponse,
    StreamEvent,
)
from qanorm.db.types import MessageRole
from qanorm.models import QAAnswer, QAEvidence, QAMessage, QAQuery, QASession
from qanorm.audit import AuditWriter
from qanorm.observability import increment_event, set_query_id, set_session_id
from qanorm.services.qa.query_service import QueryService
from qanorm.workers.stage2 import build_query_events_channel, publish_progress_event


router = APIRouter(tags=["chat"])


@router.post("/sessions/{session_id}/queries", response_model=MessageResponse)
async def create_query(
    session_id: UUID,
    payload: MessageRequest,
    db: Session = Depends(get_db_session),
    redis: redis_asyncio.Redis = Depends(get_redis_client),
) -> MessageResponse:
    """Persist a user message and create a linked query run."""

    qa_session = db.get(QASession, session_id)
    if qa_session is None:
        raise APIError(status_code=404, code="session_not_found", message="Session not found.")

    message, query = QueryService(db).create_query_from_message(
        session_id=session_id,
        content=payload.content,
        metadata_json=payload.metadata_json,
        query_type=payload.query_type,
    )
    set_session_id(str(session_id))
    set_query_id(str(query.id))
    AuditWriter(db).write(
        session_id=session_id,
        query_id=query.id,
        event_type="query_submitted",
        actor_kind="api",
        payload_json={"query_type": payload.query_type, "content_length": len(payload.content)},
    )
    increment_event("query_created", status="ok")
    await publish_progress_event(
        redis,
        query_id=query.id,
        event="query_created",
        data={"session_id": str(session_id), "message_id": str(message.id)},
    )
    return MessageResponse(
        message_id=message.id,
        session_id=session_id,
        query_id=query.id,
        role=message.role.value,
        content=message.content,
        created_at=message.created_at,
    )


@router.get("/queries/{query_id}", response_model=QueryDetailResponse)
def get_query_details(
    query_id: UUID,
    db: Session = Depends(get_db_session),
) -> QueryDetailResponse:
    """Return one query together with persisted answer and evidence rows."""

    query = db.get(QAQuery, query_id)
    if query is None:
        raise APIError(status_code=404, code="query_not_found", message="Query not found.")

    answer = db.execute(select(QAAnswer).where(QAAnswer.query_id == query.id).limit(1)).scalar_one_or_none()
    evidence_rows = list(
        db.execute(select(QAEvidence).where(QAEvidence.query_id == query.id).order_by(QAEvidence.created_at.asc())).scalars().all()
    )
    return QueryDetailResponse(
        id=query.id,
        session_id=query.session_id,
        message_id=query.message_id,
        status=query.status.value,
        query_type=query.query_type,
        query_text=query.query_text,
        requires_freshness_check=query.requires_freshness_check,
        used_open_web=query.used_open_web,
        used_trusted_web=query.used_trusted_web,
        created_at=query.created_at,
        finished_at=query.finished_at,
        answer=_serialize_answer(db, query, answer),
        evidence=[_serialize_evidence(item) for item in evidence_rows],
    )


@router.get("/queries/{query_id}/events")
async def stream_query_events(
    query_id: UUID,
    request: Request,
    redis: redis_asyncio.Redis = Depends(get_redis_client),
) -> EventSourceResponse:
    """Stream query progress events over SSE using Redis pubsub."""

    channel = build_query_events_channel(query_id)

    async def event_generator():
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            # Emit a bootstrap event so clients know the stream is active.
            bootstrap = StreamEvent(
                event="stream_ready",
                query_id=query_id,
                data={},
                created_at=datetime.now(timezone.utc),
            )
            yield {"event": bootstrap.event, "data": bootstrap.model_dump_json()}

            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0.1)
                    continue
                payload = json.loads(message["data"])
                yield {"event": payload["event"], "data": json.dumps(payload, ensure_ascii=False)}
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return EventSourceResponse(event_generator())


def _serialize_answer(db: Session, query: QAQuery, answer: QAAnswer | None) -> AnswerResponse | None:
    """Normalize one persisted answer row into the API contract."""

    if answer is None:
        return None
    metadata = {}
    assistant_message = db.execute(
        select(QAMessage)
        .where(QAMessage.session_id == query.session_id, QAMessage.role == MessageRole.ASSISTANT)
        .order_by(QAMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if assistant_message is not None and isinstance(assistant_message.metadata_json, dict):
        metadata = assistant_message.metadata_json
    sections = [
        AnswerSectionResponse(
            heading=str(item.get("heading", "")),
            body=str(item.get("body", "")),
            source_kind=str(item.get("source_kind", "normative")),
            citations=[
                AnswerCitationResponse(
                    title=str(citation.get("title", "")),
                    edition_label=citation.get("edition_label"),
                    locator=citation.get("locator"),
                    quote=citation.get("quote"),
                    is_normative=bool(citation.get("is_normative", False)),
                    requires_verification=bool(citation.get("requires_verification", False)),
                )
                for citation in item.get("citations", [])
                if isinstance(citation, dict)
            ],
        )
        for item in metadata.get("sections", [])
        if isinstance(item, dict)
    ]
    return AnswerResponse(
        answer_text=str(metadata.get("answer_text", answer.answer_text)),
        markdown=str(metadata.get("markdown", answer.answer_text)),
        answer_format=answer.answer_format,
        coverage_status=answer.coverage_status.value,
        has_stale_sources=answer.has_stale_sources,
        has_external_sources=answer.has_external_sources,
        assumptions=[str(item) for item in metadata.get("assumptions", [])],
        limitations=[str(item) for item in metadata.get("limitations", [])],
        warnings=[str(item) for item in metadata.get("warnings", [])],
        sections=sections,
        model_name=answer.model_name,
    )


def _serialize_evidence(evidence: QAEvidence) -> EvidenceResponse:
    """Normalize one evidence row into the transport schema."""

    return EvidenceResponse(
        id=evidence.id,
        source_kind=evidence.source_kind.value,
        source_url=evidence.source_url,
        source_domain=evidence.source_domain,
        document_id=evidence.document_id,
        document_version_id=evidence.document_version_id,
        chunk_id=evidence.chunk_id,
        locator=evidence.locator,
        locator_end=evidence.locator_end,
        edition_label=evidence.edition_label,
        quote=evidence.quote,
        chunk_text=evidence.chunk_text,
        freshness_status=evidence.freshness_status.value,
        is_normative=evidence.is_normative,
        requires_verification=evidence.requires_verification,
        relevance_score=evidence.relevance_score,
    )
