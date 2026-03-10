from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.models import Document, DocumentVersion
from qanorm.models.qa_state import QueryState
from qanorm.services.qa.document_resolver import (
    DocumentResolutionCandidate,
    DocumentResolutionStatus,
    DocumentResolver,
    expand_document_code_variants,
    normalize_short_document_reference,
)


def test_normalize_short_document_reference_inserts_missing_space() -> None:
    assert normalize_short_document_reference("СП63.13330") == "СП 63.13330"
    assert normalize_short_document_reference("ГОСТ21.501") == "ГОСТ 21.501"


def test_expand_document_code_variants_preserves_short_and_canonical_forms() -> None:
    variants = expand_document_code_variants("СП63")

    assert "СП 63" in variants
    assert "СП63" in variants


def test_document_resolver_returns_unresolved_without_document_hints() -> None:
    resolver = DocumentResolver(MagicMock())
    state = QueryState(session_id=uuid4(), query_id=uuid4(), message_id=uuid4(), query_text="Какая толщина стены нужна?")

    result = resolver.resolve(state)

    assert result.status == DocumentResolutionStatus.UNRESOLVED
    assert result.retrieval_scope == "global"


def test_document_resolver_returns_resolved_for_clear_candidate(monkeypatch) -> None:
    resolver = DocumentResolver(MagicMock())
    candidate = DocumentResolutionCandidate(
        document_id=uuid4(),
        document_version_id=uuid4(),
        normalized_code="СП 63.13330",
        display_code="СП 63.13330",
        title="Бетонные и железобетонные конструкции",
        document_type="SP",
        score=1.18,
        matched_on="normalized_code_exact+locator",
        locator_match_count=2,
    )
    monkeypatch.setattr(resolver, "_find_candidates", lambda **kwargs: [candidate])
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="п. 8.3 СП 63",
        document_hints=["СП 63"],
        locator_hints=["п. 8.3"],
    )

    result = resolver.resolve(state)

    assert result.status == DocumentResolutionStatus.RESOLVED
    assert result.retrieval_scope == "document_scoped"
    assert result.primary_candidate == candidate
    assert result.locator_hint == "п. 8.3"


def test_document_resolver_returns_ambiguous_when_top_scores_are_close(monkeypatch) -> None:
    resolver = DocumentResolver(MagicMock())
    candidates = [
        DocumentResolutionCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            normalized_code="СП 20.13330.2016",
            display_code="СП 20.13330.2016",
            title="Нагрузки и воздействия",
            document_type="SP",
            score=0.92,
            matched_on="normalized_code_prefix",
        ),
        DocumentResolutionCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            normalized_code="СП 20.13330.2020",
            display_code="СП 20.13330.2020",
            title="Нагрузки и воздействия",
            document_type="SP",
            score=0.87,
            matched_on="normalized_code_prefix",
        ),
    ]
    monkeypatch.setattr(resolver, "_find_candidates", lambda **kwargs: candidates)
    state = QueryState(
        session_id=uuid4(),
        query_id=uuid4(),
        message_id=uuid4(),
        query_text="таблица 5 СП 20",
        document_hints=["СП 20"],
        locator_hints=["таблица 5"],
    )

    result = resolver.resolve(state)

    assert result.status == DocumentResolutionStatus.AMBIGUOUS
    assert result.retrieval_scope == "global"
    assert len(result.candidates) == 2


def test_document_resolver_score_boosts_locator_matches(monkeypatch) -> None:
    resolver = DocumentResolver(MagicMock())
    monkeypatch.setattr(resolver, "_count_locator_matches", lambda **kwargs: 2)
    document = Document(id=uuid4(), normalized_code="СП 63.13330", display_code="СП 63.13330", document_type="SP")
    version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=True)

    candidate = resolver._score_candidate(
        document=document,
        version=version,
        hint="СП 63",
        locator_hint="п. 8.3",
    )

    assert candidate.locator_match_count == 2
    assert candidate.score > 1.0
    assert "+locator" in candidate.matched_on
