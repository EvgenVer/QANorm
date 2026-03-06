"""Chat query endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from redis import asyncio as redis_asyncio
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from qanorm.api.dependencies import get_db_session, get_redis_client
from qanorm.api.errors import APIError
from qanorm.api.schemas import MessageRequest, MessageResponse, StreamEvent
from qanorm.models import QASession
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
