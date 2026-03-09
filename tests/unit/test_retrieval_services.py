from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from qanorm.db.types import FreshnessStatus, StatusNormalized
from qanorm.models import Document, DocumentNode, DocumentVersion
from qanorm.services.qa.chunking_service import ChunkingConfig, build_chunk_hash, build_retrieval_chunk_drafts
from qanorm.services.qa.retrieval_estimate_service import estimate_retrieval_rollout
from qanorm.services.qa.retrieval_service import (
    RetrievalHit,
    RetrievalRequest,
    normalize_hits_to_evidence,
    retrieve_normative_evidence,
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
    exact_hit = _build_hit(score=10.0, score_source="exact")
    fts_hit = _build_hit(chunk_id=exact_hit.chunk_id, chunk_hash=exact_hit.chunk_hash, score=0.8, score_source="fts")
    vector_hit = _build_hit(score=0.7, score_source="vector")
    secondary_hit = _build_hit(score=0.2, score_source="reference")

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_exact_match_lookup", lambda session, request: [exact_hit])
    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_fts_search", lambda session, request: [fts_hit])

    async def _fake_vector_search(session, *, request, embedding_provider=None, runtime_config=None):
        return [vector_hit]

    monkeypatch.setattr("qanorm.services.qa.retrieval_service._run_vector_search", _fake_vector_search)
    monkeypatch.setattr(
        "qanorm.services.qa.retrieval_service._load_secondary_hits",
        lambda session, *, primary_hits, limit: [secondary_hit],
    )

    result = asyncio.run(retrieve_normative_evidence(MagicMock(), request=RetrievalRequest(query_text="SP 1", limit=5)))

    assert result.primary_hits[0].chunk_id == exact_hit.chunk_id
    assert "exact" in result.primary_hits[0].score_source
    assert result.secondary_hits == [secondary_hit]


def test_normalize_hits_to_evidence_deduplicates_by_chunk_hash() -> None:
    first = _build_hit()
    second = _build_hit(chunk_id=uuid4(), chunk_hash=first.chunk_hash)

    evidence = normalize_hits_to_evidence(query_id=uuid4(), hits=[first, second])

    assert len(evidence) == 1
    assert evidence[0].chunk_id == first.chunk_id


def _build_hit(
    *,
    chunk_id=None,
    chunk_hash: str | None = None,
    score: float = 1.0,
    score_source: str = "fts",
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
        locator="1",
        locator_end="1.1",
        chunk_text="chunk text",
        quote="quote",
        score=score,
        score_source=score_source,
        freshness_status=FreshnessStatus.FRESH,
    )
