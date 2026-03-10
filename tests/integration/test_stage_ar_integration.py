from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import FreshnessStatus
from qanorm.services.qa.document_resolver import (
    DocumentResolutionCandidate,
    DocumentResolutionResult,
    DocumentResolutionStatus,
)
from qanorm.services.qa.retrieval_service import (
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    retrieve_normative_evidence_with_resolution,
)


def test_930_integration_prefers_document_scoped_retrieval_for_explicit_norm_hint(monkeypatch) -> None:
    scoped_hit = _build_hit(document_code="СП 63.13330", locator="п. 8.3")
    global_hit = _build_hit(document_code="СП 20.13330", locator="п. 5.1")
    calls: list[tuple[list, str]] = []

    async def _fake_retrieve(session, *, request, embedding_provider=None, runtime_config=None):
        calls.append((list(request.document_ids), request.retrieval_scope))
        if request.document_ids:
            return RetrievalResult(primary_hits=[scoped_hit], secondary_hits=[])
        return RetrievalResult(primary_hits=[global_hit], secondary_hits=[])

    monkeypatch.setattr("qanorm.services.qa.retrieval_service.retrieve_normative_evidence", _fake_retrieve)

    resolution = DocumentResolutionResult(
        status=DocumentResolutionStatus.RESOLVED,
        retrieval_scope="document_scoped",
        matched_hint="СП 63",
        locator_hint="п. 8.3",
        primary_candidate=DocumentResolutionCandidate(
            document_id=scoped_hit.document_id,
            document_version_id=scoped_hit.document_version_id,
            normalized_code="СП 63.13330",
            display_code="СП 63.13330",
            title="Бетонные конструкции",
            document_type="SP",
            score=1.2,
            matched_on="normalized_code_exact+locator",
            locator_match_count=1,
        ),
    )

    result, metadata = asyncio.run(
        retrieve_normative_evidence_with_resolution(
            MagicMock(),
            request=RetrievalRequest(query_text="п. 8.3 СП 63", limit=5),
            resolution=resolution,
        )
    )

    assert result.primary_hits == [scoped_hit]
    assert metadata["final_scope"] == "document_scoped"
    assert metadata["fallback_used"] is False
    assert calls == [([scoped_hit.document_id], "document_scoped")]


def _build_hit(*, document_code: str, locator: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=uuid4(),
        chunk_hash="a" * 64,
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_code=document_code,
        document_title=document_code,
        document_type="SP",
        edition_label="2024",
        start_node_id=uuid4(),
        end_node_id=uuid4(),
        locator=locator,
        locator_end=locator,
        chunk_text="chunk text",
        quote="quote",
        score=1.0,
        score_source="exact",
        freshness_status=FreshnessStatus.FRESH,
    )
