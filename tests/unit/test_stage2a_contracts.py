from __future__ import annotations

from uuid import uuid4

from qanorm.stage2a.contracts import DocumentCandidateDTO, EvidenceItemDTO, RetrievalHitDTO, Stage2AAnswerDTO
from qanorm.stage2a.retrieval.engine import DocumentCandidate, RetrievalHit


def test_contract_dtos_convert_retrieval_primitives() -> None:
    document_id = uuid4()
    version_id = uuid4()
    node_id = uuid4()

    candidate = DocumentCandidate(
        document_id=document_id,
        document_version_id=version_id,
        score=0.92,
        reason="exact_alias",
        matched_value="сп 63",
        display_code="СП 63.13330.2018",
        title="Бетонные и железобетонные конструкции",
    )
    hit = RetrievalHit(
        source_kind="document_node_locator",
        score=1.0,
        document_id=document_id,
        document_version_id=version_id,
        node_id=node_id,
        retrieval_unit_id=None,
        order_index=12,
        locator="5.1",
        heading_path="Раздел 5",
        text="Требование к расчету конструкции.",
    )

    candidate_dto = DocumentCandidateDTO.from_candidate(candidate)
    hit_dto = RetrievalHitDTO.from_hit(hit)
    evidence = EvidenceItemDTO.from_hit(hit, evidence_id="ev-1")
    answer = Stage2AAnswerDTO(
        mode="direct",
        answer_text="Короткий grounded ответ.",
        evidence=[evidence],
    )

    assert candidate_dto.display_code == "СП 63.13330.2018"
    assert hit_dto.locator == "5.1"
    assert evidence.evidence_id == "ev-1"
    assert answer.evidence[0].document_id == document_id
