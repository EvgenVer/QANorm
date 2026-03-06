"""Session endpoints for Stage 2 chat transport."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.api.dependencies import get_db_session
from qanorm.api.errors import APIError
from qanorm.api.schemas import CreateSessionRequest, MessageResponse, SessionResponse
from qanorm.models import QAMessage, QASession
from qanorm.services.qa.session_service import SessionService


router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    payload: CreateSessionRequest,
    db: Session = Depends(get_db_session),
) -> SessionResponse:
    """Create a new chat session."""

    qa_session = SessionService(db).create_session(
        channel=payload.channel,
        external_user_id=payload.external_user_id,
        external_chat_id=payload.external_chat_id,
    )
    return SessionResponse.model_validate(qa_session)


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(db: Session = Depends(get_db_session)) -> list[SessionResponse]:
    """List known sessions in reverse chronological order."""

    stmt = select(QASession).order_by(QASession.created_at.desc())
    sessions = list(db.execute(stmt).scalars().all())
    return [SessionResponse.model_validate(item) for item in sessions]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: UUID, db: Session = Depends(get_db_session)) -> SessionResponse:
    """Load one session by id."""

    qa_session = db.get(QASession, session_id)
    if qa_session is None:
        raise APIError(status_code=404, code="session_not_found", message="Session not found.")
    return SessionResponse.model_validate(qa_session)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def list_messages(session_id: UUID, db: Session = Depends(get_db_session)) -> list[MessageResponse]:
    """List persisted messages for one session."""

    qa_session = db.get(QASession, session_id)
    if qa_session is None:
        raise APIError(status_code=404, code="session_not_found", message="Session not found.")

    stmt = select(QAMessage).where(QAMessage.session_id == session_id).order_by(QAMessage.created_at.asc())
    messages = list(db.execute(stmt).scalars().all())
    return [
        MessageResponse(
            message_id=item.id,
            session_id=item.session_id,
            query_id=None,
            role=item.role.value,
            content=item.content,
            created_at=item.created_at,
        )
        for item in messages
    ]
