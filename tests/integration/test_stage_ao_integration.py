from __future__ import annotations

from types import SimpleNamespace
import asyncio
from uuid import UUID

from sqlalchemy import select

from qanorm.db.session import session_scope
from qanorm.db.types import MessageRole, SessionChannel
from qanorm.integrations.telegram.bot import (
    _handle_new_session,
    _handle_text_message,
    ensure_telegram_session,
    submit_telegram_query,
)
from qanorm.models import AuditEvent, QAMessage, QAQuery


class _FakeTelegramMessage:
    """Small aiogram-like stub used to exercise adapter handlers."""

    def __init__(self, *, chat_id: int, user_id: int, text: str | None = None) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def test_433_integration_new_command_creates_or_resumes_telegram_session() -> None:
    message = _FakeTelegramMessage(chat_id=401, user_id=501, text="/new")

    asyncio.run(_handle_new_session(message))

    with session_scope() as session:
        stored_session = session.execute(
            select(QAQuery).where(QAQuery.session_id == ensure_telegram_session(chat_id=401, user_id=501).session_id).limit(1)
        ).scalar_one_or_none()
    binding = ensure_telegram_session(chat_id=401, user_id=501)

    assert binding.created is False
    assert isinstance(binding.session_id, UUID)
    assert message.answers
    assert str(binding.session_id) in message.answers[0]
    assert stored_session is None


def test_434_integration_text_message_submits_query_and_returns_pending_notice(monkeypatch) -> None:
    message = _FakeTelegramMessage(chat_id=402, user_id=502, text="Check smoke query")

    monkeypatch.setattr(
        "qanorm.integrations.telegram.bot.load_latest_answer_markdown",
        lambda *, query_id: None,
    )

    asyncio.run(_handle_text_message(message))

    with session_scope() as session:
        query = session.execute(select(QAQuery).order_by(QAQuery.created_at.desc()).limit(1)).scalar_one()
        query_id = query.id
        query_type = query.query_type
        session_channel = query.session.channel
        stored_message = session.execute(
            select(QAMessage)
            .where(QAMessage.id == query.message_id, QAMessage.session_id == query.session_id, QAMessage.role == MessageRole.USER)
            .limit(1)
        ).scalar_one()
        stored_message_content = stored_message.content
        audit_event = session.execute(
            select(AuditEvent).where(AuditEvent.query_id == query.id, AuditEvent.event_type == "telegram_query_submitted").limit(1)
        ).scalar_one()
        audit_actor_kind = audit_event.actor_kind

    assert query_type == "telegram_chat"
    assert session_channel == SessionChannel.TELEGRAM
    assert stored_message_content == "Check smoke query"
    assert audit_actor_kind == "telegram"
    assert message.answers
    assert str(query_id) in message.answers[0]


def test_435_integration_text_message_sends_chunked_answer_when_result_exists(monkeypatch) -> None:
    message = _FakeTelegramMessage(chat_id=403, user_id=503, text="Return answer")
    binding = ensure_telegram_session(chat_id=403, user_id=503)
    query_id = submit_telegram_query(session_id=binding.session_id, text="Return answer")

    monkeypatch.setattr(
        "qanorm.integrations.telegram.bot.load_latest_answer_markdown",
        lambda *, query_id: "## Heading\n\n- item 1\n- item 2",
    )

    asyncio.run(_handle_text_message(message))

    assert len(message.answers) == 1
    assert "<b>Heading</b>" in message.answers[0]
    assert "item 1" in message.answers[0]
    assert query_id is not None
