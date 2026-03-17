"""Streamlit UI for the Stage 2A / Stage 2B grounded QA flow."""

from __future__ import annotations

from typing import Any

import streamlit as st

from qanorm.stage2a.config import get_stage2a_config
from qanorm.stage2a.contracts import RuntimeEventDTO, Stage2AChatSessionDTO, Stage2AConversationalQueryRequest
from qanorm.stage2a.runtime import Stage2AConversationalQueryResult, Stage2AQueryResult, Stage2ARuntime
from qanorm.stage2a.ui.rendering import format_runtime_event, iter_markdown_chunks
from qanorm.stage2a.ui.session_state import (
    create_new_ui_session,
    ensure_ui_sessions,
    get_active_ui_session,
    list_ui_sessions,
    replace_active_ui_session,
    reset_active_ui_session,
    set_active_ui_session,
)


def main() -> None:
    """Render the Stage 2A / Stage 2B chat UI."""

    config = get_stage2a_config()
    st.set_page_config(page_title=config.ui.title, layout="wide")
    st.title(config.ui.title)
    st.caption("Agentic RAG over the local Stage 1 normative corpus.")

    runtime = _get_runtime()
    ensure_ui_sessions(st.session_state, config=config)
    _render_session_sidebar()
    active_session = get_active_ui_session(st.session_state)

    for entry in active_session.messages:
        with st.chat_message(entry.role):
            st.markdown(entry.content)
            if entry.role == "assistant" and entry.result_payload:
                _render_result_panels(entry.result_payload)

    query_text = st.chat_input("Задайте инженерный нормативный вопрос")
    if not query_text:
        return

    with st.chat_message("user"):
        st.markdown(query_text)

    stored_session = active_session
    with st.chat_message("assistant"):
        answer_placeholder = st.empty()
        debug_placeholder = st.empty()
        streamed_events: list[RuntimeEventDTO] = []
        conversational_result: Stage2AConversationalQueryResult | None = None

        for event in runtime.stream_conversation_turn(
            Stage2AConversationalQueryRequest(
                query_text=query_text,
                chat_session=active_session,
            )
        ):
            streamed_events.append(event)
            if event.event_type == "answer_ready":
                conversational_result = Stage2AConversationalQueryResult.model_validate(
                    event.payload["conversation_result"]
                )
                continue
            _render_runtime_trace(debug_placeholder, streamed_events, expanded=config.ui.show_debug_panel)

        if conversational_result is None:
            raise RuntimeError("Runtime stream finished without final conversational result")

        _render_runtime_trace(debug_placeholder, streamed_events, expanded=False)
        answer_text = conversational_result.result.answer.answer_text
        if config.ui.stream:
            _stream_markdown_answer(answer_placeholder, answer_text)
        answer_placeholder.markdown(answer_text)

        stored_session = _store_result_payload(
            conversational_result.chat_session,
            conversational_result.result,
            streamed_events,
        )
        _render_result_panels(stored_session.last_result or conversational_result.result)

    replace_active_ui_session(st.session_state, stored_session)


@st.cache_resource(show_spinner=False)
def _get_runtime() -> Stage2ARuntime:
    """Keep one runtime instance per Streamlit process."""

    return Stage2ARuntime()


def _render_session_sidebar() -> None:
    """Render sidebar controls for multiple local chat sessions."""

    config = get_stage2a_config()
    sessions = list_ui_sessions(st.session_state)
    active_session = get_active_ui_session(st.session_state)

    with st.sidebar:
        st.subheader("Сессии")
        if st.button("Новая сессия", use_container_width=True):
            create_new_ui_session(st.session_state, config=config)
            st.rerun()
        if st.button("Сбросить текущую сессию", use_container_width=True):
            reset_active_ui_session(st.session_state, config=config)
            st.rerun()

        options = [session.session_id for session in sessions]
        selected_session_id = st.radio(
            "Активная сессия",
            options=options,
            index=options.index(active_session.session_id),
            format_func=lambda session_id: _session_title(session_id),
        )
        if selected_session_id != active_session.session_id:
            set_active_ui_session(st.session_state, selected_session_id)
            st.rerun()


def _session_title(session_id: str) -> str:
    sessions = {session.session_id: session for session in list_ui_sessions(st.session_state)}
    session = sessions[session_id]
    return session.title


def _stream_markdown_answer(placeholder: Any, text: str) -> None:
    rendered = ""
    for chunk in iter_markdown_chunks(text):
        rendered += chunk
        placeholder.markdown(f"{rendered}\n\n`...`")
    placeholder.markdown(text)


def _render_runtime_trace(placeholder: Any, events: list[RuntimeEventDTO], *, expanded: bool) -> None:
    with placeholder.container():
        with st.expander("Ход агента", expanded=expanded):
            if not events:
                st.write("События пока не поступили.")
                return
            for event in events:
                if event.event_type == "answer_ready":
                    continue
                st.markdown(format_runtime_event(event))


def _store_result_payload(
    chat_session: Stage2AChatSessionDTO,
    result: Stage2AQueryResult,
    runtime_events: list[RuntimeEventDTO],
) -> Stage2AChatSessionDTO:
    result_payload = result.model_dump(mode="json")
    result_payload["runtime_events"] = [event.model_dump(mode="json") for event in runtime_events]

    messages = list(chat_session.messages)
    if messages and messages[-1].role == "assistant":
        messages[-1] = messages[-1].model_copy(update={"result_payload": result_payload})

    return chat_session.model_copy(
        update={
            "messages": messages,
            "last_result": result_payload,
            "runtime_events": runtime_events,
        }
    )


def _render_result_panels(result_payload: Stage2AQueryResult | dict[str, Any]) -> None:
    result = result_payload if isinstance(result_payload, dict) else result_payload.model_dump(mode="json")

    answer = result["answer"]
    controller = result["controller"]

    st.caption(f"Режим ответа: `{answer['mode']}`")

    with st.expander("Evidence", expanded=True):
        if not answer["evidence"]:
            st.write("Подтвержденные evidence не выбраны.")
        for item in answer["evidence"]:
            st.markdown(
                f"**{item['evidence_id']}**  \n"
                f"Citation: `{_format_ui_citation(item)}`  \n"
                f"Источник: `{item['source_kind']}`  \n"
                f"Текст: {item['text']}"
            )

    with st.expander("Ограничения", expanded=bool(answer["limitations"])):
        if answer["limitations"]:
            for item in answer["limitations"]:
                st.write(f"- {item}")
        else:
            st.write("Явные ограничения не зафиксированы.")

    if get_stage2a_config().ui.show_debug_panel:
        with st.expander("Debug Trace", expanded=False):
            runtime_events = result.get("runtime_events", [])
            if runtime_events:
                for item in runtime_events:
                    event = RuntimeEventDTO.model_validate(item)
                    if event.event_type == "answer_ready":
                        continue
                    st.markdown(format_runtime_event(event))
            else:
                st.write(f"Policy hint: {controller['policy_hint']}")
                st.write(f"Selected evidence ids: {', '.join(controller['selected_evidence_ids']) or '-'}")
                for entry in answer["debug_trace"]:
                    st.code(entry)


def _format_ui_citation(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("document_display_code"):
        parts.append(str(item["document_display_code"]))
    if item.get("locator"):
        parts.append(f"п. {item['locator']}")
    if item.get("heading_path"):
        parts.append(str(item["heading_path"]))
    return " | ".join(parts) if parts else "-"


if __name__ == "__main__":
    main()
