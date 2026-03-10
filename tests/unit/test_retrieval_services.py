from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from qanorm.db.types import FreshnessStatus, StatusNormalized
from qanorm.models import Document, DocumentNode, DocumentVersion
from qanorm.services.qa.chunking_service import ChunkingConfig, build_chunk_hash, build_retrieval_chunk_drafts
from qanorm.services.qa.document_resolver import (
    DocumentResolutionCandidate,
    DocumentResolutionResult,
    DocumentResolutionStatus,
)
from qanorm.services.qa.retrieval_estimate_service import estimate_retrieval_rollout
from qanorm.services.qa.retrieval_service import (
    RetrievalHit,
    RetrievalRequest,
    normalize_hits_to_evidence,
    retrieve_normative_evidence,
    retrieve_normative_evidence_with_resolution,
)
from tests.unit.test_provider_registry import _runtime_config


def test_build_retrieval_chunk_drafts_groups_related_nodes() -> None:
    title_id = uuid4()
    section_id = uuid4()
    point_id = uuid4()
    subpoint_id = uuid4()
    paragraph_id = uuid4()
    second_point_id = uuid4()
    nodes = [
        DocumentNode(id=title_id, document_version_id=uuid4(), node_type="title", title="SP 20.13330.2016", text="SP 20.13330.2016", order_index=1),
        DocumentNode(id=section_id, document_version_id=uuid4(), parent_node_id=title_id, node_type="section", label="1", title="General", text="Section 1", order_index=2),
        DocumentNode(id=point_id, document_version_id=uuid4(), parent_node_id=section_id, node_type="point", label="1", title="Requirement", text="Requirement", order_index=3),
        DocumentNode(id=subpoint_id, document_version_id=uuid4(), parent_node_id=point_id, node_type="subpoint", label="1.1", title="Sub requirement", text="Sub requirement", order_index=4),
        DocumentNode(id=paragraph_id, document_version_id=uuid4(), parent_node_id=point_id, node_type="paragraph", text="Additional explanation for the same requirement", order_index=5),
        DocumentNode(id=second_point_id, document_version_id=uuid4(), parent_node_id=section_id, node_type="point", label="2", title="Second requirement", text="Second requirement", order_index=6),
    ]

    drafts = build_retrieval_chunk_drafts(nodes, config=ChunkingConfig(min_tokens=1, max_tokens=80))

    assert len(drafts) == 2
    assert drafts[0].start_node_id == point_id
    assert drafts[0].end_node_id == paragraph_id
    assert "SP 20.13330.2016" in (drafts[0].heading_path or "")
    assert drafts[1].start_node_id == second_point_id


def test_build_chunk_hash_normalizes_whitespace() -> None:
    left = build_chunk_hash("Clause   one")
    right = build_chunk_hash("Clause one")

    assert left == right


def test_estimate_retrieval_rollout_uses_local_provider_cost_zero(monkeypatch) -> None:
    session = MagicMock()
    document = Document(
        id=uuid4(),
        normalized_code="SP 1",
        display_code="SP 1",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=True)
    document.current_version_id = version.id
    session.execute.return_value.all.return_value = [(document, version)]

    class _FakeNodeRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def list_for_document_version(self, document_version_id):
            return [
                DocumentNode(id=uuid4(), document_version_id=document_version_id, node_type="title", title="SP 1", text="SP 1", order_index=1),
                DocumentNode(id=uuid4(), document_version_id=document_version_id, node_type="point", label="1", title="Req", text="Requirement text", order_index=2),
            ]

    monkeypatch.setattr("qanorm.services.qa.retrieval_estimate_service.DocumentNodeRepository", _FakeNodeRepository)

    estimate = estimate_retrieval_rollout(session, runtime_config=_runtime_config())

    assert estimate.active_document_count == 1
    assert estimate.estimated_chunk_count == 1
    assert estimate.estimated_embedding_cost == 0.0


def test_retrieve_normative_evidence_merges_ranked_sources(monkeypatch) -> None:
    exact_hit = _build_hit(score=10.0, score_source="exact", locator="п. 8.3", quote="Требование пункта 8.3.")
    fts_hit = _build_hit(chunk_id=exact_hit.chunk_id, chunk_hash=exact_hit.chunk_hash, score=0.8, score_source="fts", locator="п. 8.3", quote="Требование пункта 8.3.")
    vector_hit = _build_hit(score=0.7, score_source="vector", locator="п. 5.1", quote="Общие положения.")
    secondary_hit = _build_hit(score=0.2, score_source="reference", locator="таблица 5", quote="Связанный документ.")

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_exact_match_lookup", lambda session, request, query_rewrite: [exact_hit])
    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_fts_search", lambda session, request, query_rewrite: [fts_hit])

    async def _fake_vector_search(session, *, request, query_rewrite, embedding_provider=None, runtime_config=None):
        return [vector_hit]

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_vector_search", _fake_vector_search)
    monkeypatch.setattr(
        "qanorm.services.qa.retrieval_service._load_secondary_hits",
        lambda session, *, primary_hits, limit: [secondary_hit],
    )

    result = asyncio.run(
        retrieve_normative_evidence(
            MagicMock(),
            request=RetrievalRequest(query_text="SP 1", document_hint="SP 1", locator_hint="п. 8.3", limit=5),
        )
    )

    assert result.primary_hits[0].chunk_id == exact_hit.chunk_id
    assert result.primary_hits[0].selection_tier == "primary"
    assert "exact" in result.primary_hits[0].score_source
    assert result.secondary_hits[-1].chunk_id == secondary_hit.chunk_id


def test_normalize_hits_to_evidence_deduplicates_by_chunk_hash() -> None:
    first = _build_hit()
    second = _build_hit(chunk_id=uuid4(), chunk_hash=first.chunk_hash)

    evidence = normalize_hits_to_evidence(query_id=uuid4(), hits=[first, second])

    assert len(evidence) == 1
    assert evidence[0].chunk_id == first.chunk_id
    assert evidence[0].selection_metadata["selection_tier"] == "candidate"


def test_normalize_hits_to_evidence_preserves_selection_metadata() -> None:
    hit = replace(_build_hit(), selection_tier="primary", retrieval_metadata={"ranking_rationale": ["exact_match"]})

    evidence = normalize_hits_to_evidence(query_id=uuid4(), hits=[hit])

    assert evidence[0].selection_metadata["selection_tier"] == "primary"
    assert evidence[0].selection_metadata["ranking_rationale"] == ["exact_match"]


def test_retrieve_normative_evidence_with_resolution_prefers_scoped_hits(monkeypatch) -> None:
    scoped_hit = _build_hit(locator="п. 8.3", locator_end="п. 8.3")
    calls: list[tuple[list, str, str | None]] = []

    async def _fake_retrieve(session, *, request, embedding_provider=None, runtime_config=None):
        calls.append((list(request.document_ids), request.retrieval_scope, request.locator_hint))
        return type("Result", (), {"primary_hits": [scoped_hit], "secondary_hits": [], "all_hits": [scoped_hit]})()

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
            title="Title",
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
    assert metadata["fallback_used"] is False
    assert calls == [([scoped_hit.document_id], "document_scoped", "п. 8.3")]


def test_retrieve_normative_evidence_with_resolution_falls_back_to_global_when_scoped_is_empty(monkeypatch) -> None:
    scoped_result = type("Result", (), {"primary_hits": [], "secondary_hits": [], "all_hits": []})()
    global_hit = _build_hit()
    global_result = type("Result", (), {"primary_hits": [global_hit], "secondary_hits": [], "all_hits": [global_hit]})()
    calls: list[tuple[list, str]] = []

    async def _fake_retrieve(session, *, request, embedding_provider=None, runtime_config=None):
        calls.append((list(request.document_ids), request.retrieval_scope))
        return scoped_result if request.document_ids else global_result

    monkeypatch.setattr("qanorm.services.qa.retrieval_service.retrieve_normative_evidence", _fake_retrieve)
    resolution = DocumentResolutionResult(
        status=DocumentResolutionStatus.RESOLVED,
        retrieval_scope="document_scoped",
        matched_hint="СП 20",
        locator_hint="таблица 5",
        primary_candidate=DocumentResolutionCandidate(
            document_id=uuid4(),
            document_version_id=uuid4(),
            normalized_code="СП 20.13330",
            display_code="СП 20.13330",
            title="Title",
            document_type="SP",
            score=1.0,
            matched_on="normalized_code_prefix",
        ),
    )

    result, metadata = asyncio.run(
        retrieve_normative_evidence_with_resolution(
            MagicMock(),
            request=RetrievalRequest(query_text="таблица 5 СП 20", limit=5),
            resolution=resolution,
        )
    )

    assert result.primary_hits == [global_hit]
    assert metadata["fallback_used"] is True
    assert calls == [([resolution.primary_candidate.document_id], "document_scoped"), ([], "global")]


def _build_hit(
    *,
    chunk_id=None,
    chunk_hash: str | None = None,
    score: float = 1.0,
    score_source: str = "fts",
    locator: str = "1",
    locator_end: str = "1.1",
    quote: str = "quote",
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id or uuid4(),
        chunk_hash=chunk_hash or ("a" * 64),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_code="SP 1",
        document_title="Title",
        document_type="SP",
        edition_label="2024",
        start_node_id=uuid4(),
        end_node_id=uuid4(),
        locator=locator,
        locator_end=locator_end,
        chunk_text="chunk text",
        quote=quote,
        score=score,
        score_source=score_source,
        freshness_status=FreshnessStatus.FRESH,
    )
