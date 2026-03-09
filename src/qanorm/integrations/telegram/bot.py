"""Telegram adapter that reuses the shared Stage 2 session and query services."""

from __future__ import annotations

import html
from dataclasses import dataclass
from uuid import UUID

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message

from qanorm.audit import AuditWriter
from qanorm.db.types import SessionChannel
from qanorm.db.session import session_scope
from qanorm.repositories import QAAnswerRepository
from qanorm.services.qa.query_service import QueryService
from qanorm.services.qa.session_service import SessionService
from qanorm.settings import RuntimeConfig, get_settings


@dataclass(slots=True, frozen=True)
class TelegramSessionBinding:
    """Resolved mapping between one Telegram chat and one qa_session row."""

    session_id: UUID
    created: bool


def build_telegram_bot(runtime_config: RuntimeConfig | None = None) -> tuple[Bot, Dispatcher]:
    """Construct the Bot and Dispatcher pair for the configured Telegram channel."""

    config = runtime_config or get_settings()
    bot = Bot(
        token=config.env.telegram_bot_token or "",
        default=DefaultBotProperties(parse_mode=config.qa.telegram.parse_mode),
    )
    dispatcher = Dispatcher()
    dispatcher.message.register(_handle_new_session, Command("new"))
    dispatcher.message.register(_handle_text_message, F.text)
    return bot, dispatcher


async def run_telegram_bot(runtime_config: RuntimeConfig | None = None) -> None:
    """Start long-polling Telegram delivery for the shared Stage 2 backend."""

    config = runtime_config or get_settings()
    bot, dispatcher = build_telegram_bot(config)
    await dispatcher.start_polling(
        bot,
        polling_timeout=config.qa.telegram.long_polling_timeout_seconds,
    )


def ensure_telegram_session(*, chat_id: int, user_id: int | None = None) -> TelegramSessionBinding:
    """Resume or create the qa_session bound to one Telegram chat."""

    with session_scope() as session:
        service = SessionService(session)
        existing = service.resume_session(
            channel=SessionChannel.TELEGRAM,
            external_chat_id=str(chat_id),
            external_user_id=str(user_id) if user_id is not None else None,
        )
        if existing is not None:
            return TelegramSessionBinding(session_id=existing.id, created=False)

        created = service.create_session(
            channel=SessionChannel.TELEGRAM,
            external_chat_id=str(chat_id),
            external_user_id=str(user_id) if user_id is not None else None,
        )
        return TelegramSessionBinding(session_id=created.id, created=True)


def submit_telegram_query(*, session_id: UUID, text: str) -> UUID:
    """Persist one Telegram message through the shared query service."""

    with session_scope() as session:
        _, query = QueryService(session).create_query_from_message(
            session_id=session_id,
            content=text,
            metadata_json={"channel": "telegram"},
            query_type="telegram_chat",
        )
        AuditWriter(session).write(
            session_id=session_id,
            query_id=query.id,
            event_type="telegram_query_submitted",
            actor_kind="telegram",
            payload_json={"content_length": len(text)},
        )
        return query.id


def load_latest_answer_markdown(*, query_id: UUID) -> str | None:
    """Return the persisted markdown answer for one query when it already exists."""

    with session_scope() as session:
        answer = QAAnswerRepository(session).get_by_query(query_id)
        if answer is None:
            return None
        return answer.answer_text


def format_answer_for_telegram(markdown: str, *, max_length: int = 3500) -> list[str]:
    """Convert markdown-like content into Telegram-safe HTML chunks."""

    lines: list[str] = []
    for raw_line in html.escape(markdown).replace("\r\n", "\n").splitlines():
        if raw_line.startswith("### "):
            lines.append(f"<b>{raw_line[4:]}</b>")
        elif raw_line.startswith("## "):
            lines.append(f"<b>{raw_line[3:]}</b>")
        elif raw_line.startswith("- "):
            lines.append(f"• {raw_line[2:]}")
        else:
            lines.append(raw_line)
    text = "\n".join(lines)
    return chunk_telegram_text(text, max_length=max_length)


def chunk_telegram_text(text: str, *, max_length: int) -> list[str]:
    """Split long text on paragraph boundaries so Telegram accepts the payload."""

    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, max_length)
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = max_length
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


async def _handle_new_session(message: Message) -> None:
    """Create a new Telegram-bound qa_session on demand."""

    binding = ensure_telegram_session(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else None,
    )
    await message.answer(
        f"Новая сессия {'создана' if binding.created else 'возобновлена'}: <code>{binding.session_id}</code>",
    )


async def _handle_text_message(message: Message) -> None:
    """Route a Telegram text message into the shared query lifecycle."""

    if not message.text:
        return

    config = get_settings()
    binding = ensure_telegram_session(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else None,
    )
    query_id = submit_telegram_query(session_id=binding.session_id, text=message.text)
    answer = load_latest_answer_markdown(query_id=query_id)
    if answer is None:
        await message.answer(
            "Запрос принят. Ответ будет доступен после завершения оркестрации.\n"
            f"<code>{query_id}</code>",
        )
        return

    for chunk in format_answer_for_telegram(answer, max_length=config.qa.telegram.max_message_length):
        await message.answer(chunk)
