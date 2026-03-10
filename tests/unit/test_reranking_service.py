from __future__ import annotations

import asyncio
from uuid import uuid4

from qanorm.db.types import FreshnessStatus
from qanorm.services.qa.query_rewriter import QueryRewrite
from qanorm.services.qa.reranking_service import RerankingService
from qanorm.services.qa.retrieval_service import RetrievalHit


def test_reranking_service_prefers_locator_exact_match() -> None:
    query_rewrite = QueryRewrite(
        exact_query="СП 63 п. 8.3",
        lexical_query="СП 63 п. 8.3 армирование колонн",
        semantic_query="Что требует СП 63 п. 8.3 по армированию колонн",
        document_hint="СП 63",
        locator_hint="п. 8.3",
        keywords=["СП 63", "п. 8.3", "армирование", "колонн"],
    )
    exact_hit = _build_hit(score_source="exact+fts", locator="п. 8.3", quote="В пункте 8.3 даны требования к армированию колонн.")
    noisy_hit = _build_hit(score_source="vector", locator="п. 5.1", quote="Текст про снеговые нагрузки.")

    selection = asyncio.run(
        RerankingService().select_hits(
            query_rewrite=query_rewrite,
            hits=[noisy_hit, exact_hit],
            primary_limit=2,
            secondary_limit=2,
        )
    )

    assert selection.primary_hits[0].chunk_id == exact_hit.chunk_id
    assert selection.primary_hits[0].selection_tier == "primary"
    assert "locator_match" in selection.primary_hits[0].retrieval_metadata["ranking_rationale"]
    assert all(item.chunk_id != exact_hit.chunk_id for item in selection.secondary_hits)


def _build_hit(
    *,
    score_source: str,
    locator: str,
    quote: str,
) -> RetrievalHit:
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
        score=0.7,
        score_source=score_source,
        freshness_status=FreshnessStatus.FRESH,
    )
