"""Helpers for bounded in-session Stage 2B chat memory."""

from __future__ import annotations

from typing import Iterable

from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import (
    ConversationMemoryDTO,
    ConversationMessageDTO,
    RuntimeEventDTO,
    Stage2AAnswerDTO,
    Stage2AChatSessionDTO,
)


def create_chat_session(
    session_id: str,
    *,
    title: str | None = None,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Create one empty browser-scoped chat session."""

    cfg = config or get_stage2a_config()
    safe_title = _truncate_text((title or "Новая сессия").strip() or "Новая сессия", cfg.conversation.max_session_title_chars)
    return Stage2AChatSessionDTO(session_id=session_id, title=safe_title)


def append_message(
    session: Stage2AChatSessionDTO,
    *,
    role: str,
    content: str,
    answer_mode: str | None = None,
    result_payload: dict | None = None,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Append one message and enforce the bounded transcript policy."""

    cfg = config or get_stage2a_config()
    message = ConversationMessageDTO(
        role=role,  # type: ignore[arg-type]
        content=content,
        answer_mode=answer_mode,  # type: ignore[arg-type]
        result_payload=result_payload,
    )
    messages = [*session.messages, message][-cfg.conversation.max_messages :]
    updated_title = session.title
    if role == "user" and session.title == "Новая сессия":
        updated_title = _truncate_text(_one_line(content), cfg.conversation.max_session_title_chars)
    return session.model_copy(update={"messages": messages, "title": updated_title})


def append_runtime_event(
    session: Stage2AChatSessionDTO,
    event: RuntimeEventDTO,
    *,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Append one runtime event and keep only the latest bounded subset."""

    cfg = config or get_stage2a_config()
    runtime_events = [*session.runtime_events, event][-cfg.conversation.max_runtime_events :]
    return session.model_copy(update={"runtime_events": runtime_events})


def replace_runtime_events(
    session: Stage2AChatSessionDTO,
    events: Iterable[RuntimeEventDTO],
    *,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Replace runtime events with a bounded normalized list."""

    cfg = config or get_stage2a_config()
    runtime_events = list(events)[-cfg.conversation.max_runtime_events :]
    return session.model_copy(update={"runtime_events": runtime_events})


def update_memory_after_answer(
    session: Stage2AChatSessionDTO,
    *,
    query_text: str,
    answer: Stage2AAnswerDTO,
    config: Stage2AConfig | None = None,
) -> Stage2AChatSessionDTO:
    """Refresh memory hints and summary after one completed answer."""

    cfg = config or get_stage2a_config()
    memory = session.memory
    document_hints = _bounded_unique(
        list(memory.active_document_hints) + [item.document_display_code for item in answer.evidence if item.document_display_code],
        limit=cfg.conversation.max_document_hints,
    )
    locator_hints = _bounded_unique(
        list(memory.active_locator_hints) + [item.locator for item in answer.evidence if item.locator],
        limit=cfg.conversation.max_locator_hints,
    )
    open_threads = _update_open_threads(
        current_threads=memory.open_threads,
        query_text=query_text,
        answer=answer,
        limit=cfg.conversation.max_open_threads,
    )
    summary = build_conversation_summary(
        messages=session.messages,
        document_hints=document_hints,
        locator_hints=locator_hints,
        open_threads=open_threads,
        max_chars=cfg.conversation.max_summary_chars,
    )
    updated_memory = ConversationMemoryDTO(
        conversation_summary=summary,
        active_document_hints=document_hints,
        active_locator_hints=locator_hints,
        open_threads=open_threads,
    )
    return session.model_copy(update={"memory": updated_memory, "last_result": answer.model_dump(mode="json")})


def build_conversation_summary(
    *,
    messages: list[ConversationMessageDTO],
    document_hints: list[str],
    locator_hints: list[str],
    open_threads: list[str],
    max_chars: int,
) -> str:
    """Build one compact deterministic session summary from the latest conversation state."""

    lines: list[str] = []
    if document_hints:
        lines.append(f"Документы в фокусе: {', '.join(document_hints)}.")
    if locator_hints:
        lines.append(f"Локаторы в фокусе: {', '.join(locator_hints)}.")
    if open_threads:
        lines.append(f"Незакрытые темы: {'; '.join(open_threads)}.")

    recent_messages = messages[-4:]
    if recent_messages:
        rendered_messages = " | ".join(
            f"{item.role}: {_truncate_text(_one_line(item.content), 180)}" for item in recent_messages
        )
        lines.append(f"Последние сообщения: {rendered_messages}")

    summary = " ".join(lines).strip()
    return _truncate_text(summary, max_chars)


def _update_open_threads(
    *,
    current_threads: list[str],
    query_text: str,
    answer: Stage2AAnswerDTO,
    limit: int,
) -> list[str]:
    threads = list(current_threads)
    cleaned_query = _one_line(query_text)

    if answer.mode in {"partial", "clarify"}:
        thread_parts = [cleaned_query]
        if answer.limitations:
            thread_parts.append(_truncate_text("; ".join(answer.limitations), 200))
        threads.append(" :: ".join(part for part in thread_parts if part))
    elif answer.mode == "direct":
        query_tokens = {token for token in cleaned_query.lower().split() if token}
        remaining_threads: list[str] = []
        for thread in threads:
            thread_tokens = {token for token in thread.lower().split() if token}
            if query_tokens and query_tokens & thread_tokens:
                continue
            remaining_threads.append(thread)
        threads = remaining_threads

    return _bounded_unique(threads, limit=limit)


def _bounded_unique(values: Iterable[str | None], *, limit: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_value in values:
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    if len(ordered) <= limit:
        return ordered
    return ordered[-limit:]


def _truncate_text(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."


def _one_line(text: str) -> str:
    return " ".join(text.split())
