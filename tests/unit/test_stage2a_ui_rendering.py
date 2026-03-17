from __future__ import annotations

from qanorm.stage2a.contracts import RuntimeEventDTO
from qanorm.stage2a.ui.rendering import format_runtime_event, iter_markdown_chunks


def test_iter_markdown_chunks_preserves_newlines_and_content() -> None:
    text = "Первая строка.\nВторая строка.\nТретья строка."

    chunks = list(iter_markdown_chunks(text, chunk_size=10))

    assert "".join(chunks) == text
    assert any("\n" in chunk for chunk in chunks)


def test_iter_markdown_chunks_rejects_non_positive_chunk_size() -> None:
    try:
        list(iter_markdown_chunks("text", chunk_size=0))
    except ValueError as error:
        assert "chunk_size" in str(error)
    else:
        raise AssertionError("chunk_size=0 must raise ValueError")


def test_format_runtime_event_includes_effective_query_details() -> None:
    event = RuntimeEventDTO(
        event_type="query_rewritten",
        message="Построен effective query.",
        payload={"effective_query": "Контекст беседы: СП 63.13330.2018\nВопрос: А для фундаментов?"},
    )

    rendered = format_runtime_event(event)

    assert "query_rewritten" in rendered
    assert "effective_query" in rendered
    assert "СП 63.13330.2018" in rendered


def test_format_runtime_event_includes_tool_observation() -> None:
    event = RuntimeEventDTO(
        event_type="tool_finished",
        message="Tool completed.",
        payload={
            "tool_name": "lookup_locator",
            "observation": "Found locator 10.3.8 in the active document and expanded the surrounding context.",
        },
    )

    rendered = format_runtime_event(event)

    assert "tool_finished" in rendered
    assert "lookup_locator" in rendered
    assert "10.3.8" in rendered
