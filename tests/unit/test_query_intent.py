from __future__ import annotations

from qanorm.agents.planner import QueryIntent, RetrievalMode
from qanorm.agents.planner.query_intent import (
    build_clarification_question,
    extract_document_hints,
    extract_locator_hints,
    infer_query_intent,
)


def test_extract_document_and_locator_hints_from_query() -> None:
    query = "Что требуется по п. 8.3 СП 63.13330 для колонн?"

    document_hints = extract_document_hints(query)
    locator_hints = extract_locator_hints(query)

    assert document_hints == ["СП 63.13330"]
    assert locator_hints == ["п. 8.3"]


def test_infer_query_intent_returns_clarify_for_ambiguous_document_reference() -> None:
    result = infer_query_intent("СП 20")

    assert result.intent == QueryIntent.CLARIFY
    assert result.retrieval_mode == RetrievalMode.CLARIFY
    assert result.clarification_required is True
    assert result.document_hints == ["СП 20"]
    assert result.clarification_question is not None


def test_infer_query_intent_returns_no_retrieval_for_non_engineering_smalltalk() -> None:
    result = infer_query_intent("Привет, как дела?")

    assert result.intent == QueryIntent.NO_RETRIEVAL
    assert result.retrieval_mode == RetrievalMode.NONE
    assert result.requires_normative_retrieval is False


def test_build_clarification_question_mentions_missing_document_for_locator_only() -> None:
    question = build_clarification_question(
        query_text="п. 7.4.2",
        document_hints=[],
        locator_hints=["п. 7.4.2"],
        subject=None,
    )

    assert "к какому документу" in question
