from __future__ import annotations

from qanorm.stage2a.config import load_stage2a_config
from qanorm.stage2a.contracts import RuntimeEventDTO, Stage2AAnswerDTO
from qanorm.stage2a.session_memory import (
    append_message,
    append_runtime_event,
    create_chat_session,
    replace_runtime_events,
    update_memory_after_answer,
)


def test_create_chat_session_uses_default_title_and_empty_memory() -> None:
    config = load_stage2a_config()

    session = create_chat_session("session-1", config=config)

    assert session.session_id == "session-1"
    assert session.title == "Новая сессия"
    assert session.messages == []
    assert session.memory.conversation_summary == ""


def test_append_message_trims_transcript_and_sets_first_user_title() -> None:
    config = load_stage2a_config()
    session = create_chat_session("session-1", config=config)

    for index in range(config.conversation.max_messages + 2):
        session = append_message(
            session,
            role="user",
            content=f"Сообщение {index}",
            config=config,
        )

    assert len(session.messages) == config.conversation.max_messages
    assert session.messages[0].content == "Сообщение 2"
    assert session.title == "Сообщение 0"


def test_append_runtime_event_and_replace_runtime_events_keep_bounded_tail() -> None:
    config = load_stage2a_config()
    session = create_chat_session("session-1", config=config)

    for index in range(config.conversation.max_runtime_events + 3):
        session = append_runtime_event(
            session,
            RuntimeEventDTO(event_type="tool_started", message=f"Запуск {index}"),
            config=config,
        )

    assert len(session.runtime_events) == config.conversation.max_runtime_events
    assert session.runtime_events[0].message == "Запуск 3"

    replacement = [
        RuntimeEventDTO(event_type="query_received", message="Получен запрос."),
        RuntimeEventDTO(event_type="answer_ready", message="Ответ готов.", is_terminal=True),
    ]
    session = replace_runtime_events(session, replacement, config=config)
    assert [item.event_type for item in session.runtime_events] == ["query_received", "answer_ready"]


def test_update_memory_after_partial_answer_collects_hints_and_open_threads() -> None:
    config = load_stage2a_config()
    session = create_chat_session("session-1", config=config)
    session = append_message(session, role="user", content="Что по защитному слою для фундаментов?", config=config)

    answer = Stage2AAnswerDTO(
        mode="partial",
        answer_text="Найдено только часть требований.",
        evidence=[],
        limitations=["Нужен дополнительный контекст по виду конструкции."],
    )

    session = update_memory_after_answer(
        session,
        query_text="Что по защитному слою для фундаментов?",
        answer=answer,
        config=config,
    )

    assert session.memory.open_threads
    assert "защитному слою" in session.memory.open_threads[0]
    assert "Незакрытые темы" in session.memory.conversation_summary
    assert session.last_result is not None


def test_update_memory_after_direct_answer_deduplicates_document_and_locator_hints() -> None:
    config = load_stage2a_config()
    session = create_chat_session("session-1", config=config)

    answer = Stage2AAnswerDTO.model_validate(
        {
            "mode": "direct",
            "answer_text": "Ответ по СП 63.",
            "claims": [],
            "evidence": [
                {
                    "evidence_id": "ev-0001",
                    "source_kind": "retrieval_unit_lexical",
                    "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "document_version_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "document_display_code": "СП 63.13330.2018",
                    "document_title": "СП 63",
                    "retrieval_unit_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "locator": "10.3.8",
                    "heading_path": "Раздел 10",
                    "score": 0.92,
                    "text": "Максимальный шаг арматуры в плитах ...",
                },
                {
                    "evidence_id": "ev-0002",
                    "source_kind": "retrieval_unit_lexical",
                    "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "document_version_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "document_display_code": "СП 63.13330.2018",
                    "document_title": "СП 63",
                    "retrieval_unit_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "locator": "10.3.8",
                    "heading_path": "Раздел 10",
                    "score": 0.88,
                    "text": "Продолжение фрагмента ...",
                },
            ],
            "limitations": [],
        }
    )

    session = update_memory_after_answer(
        session,
        query_text="Что СП 63 говорит про шаг арматуры?",
        answer=answer,
        config=config,
    )

    assert session.memory.active_document_hints == ["СП 63.13330.2018"]
    assert session.memory.active_locator_hints == ["10.3.8"]
    assert session.memory.open_threads == []
    assert "Документы в фокусе: СП 63.13330.2018." in session.memory.conversation_summary
