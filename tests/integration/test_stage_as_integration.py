from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import FreshnessStatus
from qanorm.services.qa.retrieval_service import RetrievalHit, RetrievalRequest, retrieve_normative_evidence


def test_940_integration_retrieval_quality_prefers_explicit_normative_locator(monkeypatch) -> None:
    exact_hit = _build_hit(score_source="exact+fts", locator="п. 8.3", quote="Пункт 8.3 задает требования к армированию колонн.")
    noisy_vector_hit = _build_hit(score_source="vector", locator="п. 5.1", quote="Текст о снеговой нагрузке.")

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_exact_match_lookup", lambda session, request, query_rewrite: [exact_hit])
    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_fts_search", lambda session, request, query_rewrite: [exact_hit])

    async def _fake_vector_search(session, *, request, query_rewrite, embedding_provider=None, runtime_config=None):
        return [noisy_vector_hit]

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_vector_search", _fake_vector_search)
    monkeypatch.setattr("qanorm.services.qa.retrieval_service._load_secondary_hits", lambda session, *, primary_hits, limit: [])

    result = asyncio.run(
        retrieve_normative_evidence(
            MagicMock(),
            request=RetrievalRequest(query_text="Что требует п. 8.3 СП 63 по армированию колонн?", limit=4),
        )
    )

    assert result.primary_hits[0].chunk_id == exact_hit.chunk_id
    assert result.primary_hits[0].selection_tier == "primary"
    assert result.secondary_hits == []


def _build_hit(*, score_source: str, locator: str, quote: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=uuid4(),
        chunk_hash=uuid4().hex * 2,
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_code="СП 63",
        document_title="СП 63",
        document_type="SP",
        edition_label="2024",
        start_node_id=uuid4(),
        end_node_id=uuid4(),
        locator=locator,
        locator_end=locator,
        chunk_text=quote,
        quote=quote,
        score=1.0,
        score_source=score_source,
        freshness_status=FreshnessStatus.FRESH,
    )
