"""Streamlit MVP for the Stage 2A grounded QA flow."""

from __future__ import annotations

from typing import Iterable

import streamlit as st

from qanorm.stage2a.config import get_stage2a_config
from qanorm.stage2a.runtime import Stage2AQueryResult, Stage2ARuntime


def main() -> None:
    """Render the Stage 2A MVP chat UI."""

    config = get_stage2a_config()
    st.set_page_config(page_title=config.ui.title, layout="wide")
    st.title(config.ui.title)
    st.caption("Agentic RAG MVP over the local Stage 1 normative corpus.")

    runtime = _get_runtime()
    _ensure_state()

    for entry in st.session_state.messages:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            if entry["role"] == "assistant" and entry.get("result"):
                _render_result_panels(entry["result"])

    query_text = st.chat_input("Задайте инженерный нормативный вопрос")
    if not query_text:
        return

    st.session_state.messages.append({"role": "user", "content": query_text})
    with st.chat_message("user"):
        st.markdown(query_text)

    with st.chat_message("assistant"):
        with st.status("Выполняется Stage 2A pipeline", expanded=config.ui.show_debug_panel):
            result = runtime.answer_query(query_text)
        response_placeholder = st.empty()
        answer_text = result.answer.answer_text
        if config.ui.stream:
            response_placeholder.write_stream(_stream_answer(answer_text))
        else:
            response_placeholder.markdown(answer_text)
        _render_result_panels(result)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer_text,
            "result": result.model_dump(mode="json"),
        }
    )


@st.cache_resource(show_spinner=False)
def _get_runtime() -> Stage2ARuntime:
    """Keep one runtime instance per Streamlit process."""

    return Stage2ARuntime()


def _ensure_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _stream_answer(text: str) -> Iterable[str]:
    for chunk in text.split():
        yield f"{chunk} "


def _render_result_panels(result_payload: Stage2AQueryResult | dict) -> None:
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
                f"Локатор: `{item['locator'] or '-'}`  \n"
                f"Заголовок: `{item['heading_path'] or '-'}`  \n"
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
            st.write(f"Policy hint: {controller['policy_hint']}")
            st.write(f"Selected evidence ids: {', '.join(controller['selected_evidence_ids']) or '-'}")
            for entry in answer["debug_trace"]:
                st.code(entry)


if __name__ == "__main__":
    main()
