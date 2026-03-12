from __future__ import annotations

from uuid import uuid4

from unittest.mock import MagicMock

from qanorm.stage2a.retrieval.engine import RetrievalEngine, RetrievalHit


def test_merge_and_rerank_hits_prioritizes_locator_hits() -> None:
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

    assert reranked[0].source_kind == "document_node_locator"
