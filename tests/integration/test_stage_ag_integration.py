from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.db.types import StatusNormalized
from qanorm.models import Document, DocumentNode, DocumentVersion
from qanorm.normalizers.structure import normalize_document_structure_text
from qanorm.services.qa.chunking_service import ChunkingConfig, build_retrieval_chunk_drafts
from qanorm.services.qa.retrieval_estimate_service import estimate_retrieval_rollout, render_retrieval_estimate_report
from tests.unit.fixture_loader import read_fixture_text
from tests.unit.test_provider_registry import _runtime_config


def test_401_integration_builds_chunks_from_stage1_fixture_and_matches_estimate(monkeypatch) -> None:
    normalized = normalize_document_structure_text(read_fixture_text("ocr", "ocr_result.txt"))
    document_id = uuid4()
    version_id = uuid4()
    nodes = [
        DocumentNode(
            id=uuid4(),
            document_version_id=version_id,
            parent_node_id=None if draft.parent_order_index is None else order_to_id[draft.parent_order_index],
            node_type=draft.node_type,
            label=draft.label,
            title=draft.title,
            text=draft.text,
            order_index=draft.order_index,
            page_from=draft.page_from,
            page_to=draft.page_to,
            char_start=draft.char_start,
            char_end=draft.char_end,
            parse_confidence=draft.parse_confidence,
        )
        for order_to_id in [
            {item.order_index: uuid4() for item in normalized.nodes}
        ]
        for draft in normalized.nodes
    ]
    drafts = build_retrieval_chunk_drafts(nodes, config=ChunkingConfig(min_tokens=1, max_tokens=120))

    session = MagicMock()
    document = Document(
        id=document_id,
        normalized_code="SP 20.13330.2016",
        display_code="SP 20.13330.2016",
        status_normalized=StatusNormalized.ACTIVE,
    )
    version = DocumentVersion(id=version_id, document_id=document_id, is_active=True)
    document.current_version_id = version.id
    session.execute.return_value.all.return_value = [(document, version)]

    class _FakeNodeRepository:
        def __init__(self, _session) -> None:
            self._session = _session

        def list_for_document_version(self, document_version_id):
            assert document_version_id == version_id
            return nodes

    monkeypatch.setattr("qanorm.services.qa.retrieval_estimate_service.DocumentNodeRepository", _FakeNodeRepository)

    estimate = estimate_retrieval_rollout(session, runtime_config=_runtime_config(), chunking_config=ChunkingConfig(min_tokens=1, max_tokens=120))
    report = render_retrieval_estimate_report(estimate)

    assert len(drafts) > 0
    assert estimate.estimated_chunk_count == len(drafts)
    assert f"- Estimated retrieval chunks: {len(drafts)}" in report
