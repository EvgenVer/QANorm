from __future__ import annotations

from uuid import uuid4

from unittest.mock import MagicMock

from qanorm.stage2a.retrieval.engine import DocumentCandidate, RetrievalEngine, RetrievalHit
from qanorm.stage2a.retrieval.query_parser import ParsedQuery


def test_merge_and_rerank_hits_prefers_contextual_retrieval_unit_over_weak_node_locator() -> None:
    retrieval = RetrievalEngine(MagicMock())
    document_id = uuid4()
    version_id = uuid4()

    locator_hit = RetrievalHit(
        source_kind="document_node_locator",
        score=0.5,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=None,
        order_index=2,
        locator="1.1",
        heading_path="Раздел 1",
        text="Требование по нагрузкам",
    )
    lexical_hit = RetrievalHit(
        source_kind="retrieval_unit_lexical",
        score=0.6,
        document_id=document_id,
        document_version_id=version_id,
        node_id=None,
        retrieval_unit_id=uuid4(),
        order_index=1,
        locator=None,
        heading_path="Раздел 1",
        text="Нагрузки и воздействия",
    )

    reranked = retrieval.merge_and_rerank_hits(
        locator_hits=[locator_hit],
        lexical_hits=[lexical_hit],
        dense_hits=[],
        explicit_locator_count=1,
    )

    assert reranked[0].source_kind == "retrieval_unit_lexical"


def test_merge_and_rerank_hits_prioritizes_retrieval_unit_context_over_document_node() -> None:
    retrieval = RetrievalEngine(MagicMock())
    document_id = uuid4()
    version_id = uuid4()

    node_hit = RetrievalHit(
        source_kind="document_node_locator",
        score=0.9,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=None,
        order_index=2,
        locator="5.1",
        heading_path="Section 5",
        text="One short line from a point.",
    )
    unit_hit = RetrievalHit(
        source_kind="retrieval_unit_context",
        score=0.92,
        document_id=document_id,
        document_version_id=version_id,
        node_id=uuid4(),
        retrieval_unit_id=uuid4(),
        order_index=1,
        locator="5.1",
        heading_path="Section 5 > 5.1",
        text="A larger contextual semantic block around the same locator.",
    )

    reranked = retrieval.merge_and_rerank_hits(
        locator_hits=[node_hit],
        lexical_hits=[unit_hit],
        dense_hits=[],
        explicit_locator_count=1,
    )

    assert reranked[0].source_kind == "retrieval_unit_context"


def test_rerank_document_candidates_prefers_latest_edition_when_query_has_no_year() -> None:
    retrieval = RetrievalEngine(MagicMock())
    latest_version = uuid4()
    old_version = uuid4()
    retrieval.document_versions.get = lambda version_id: type(
        "Version",
        (),
        {
            "is_active": version_id == latest_version,
            "is_outdated": version_id == old_version,
        },
    )()
    query = ParsedQuery(
        raw_text="Что СП 50 говорит про конденсацию влаги?",
        normalized_text="Что СП 50 говорит про конденсацию влаги?",
        explicit_document_codes=["СП 50"],
        explicit_locator_values=[],
        lexical_query="сп 50 конденсация влаги",
        lexical_tokens=["сп", "50", "конденсац", "влаг"],
    )
    candidates = [
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=old_version,
            score=1.0,
            reason="prefix_alias",
            matched_value="СП 50.13330.2012",
            display_code="СП 50.13330.2012",
            title="Тепловая защита зданий",
        ),
        DocumentCandidate(
            document_id=uuid4(),
            document_version_id=latest_version,
            score=0.96,
            reason="prefix_alias",
            matched_value="СП 50.13330.2024",
            display_code="СП 50.13330.2024",
            title="Тепловая защита зданий",
        ),
    ]

    reranked = retrieval._rerank_document_candidates(query, candidates)

    assert reranked[0].display_code == "СП 50.13330.2024"
