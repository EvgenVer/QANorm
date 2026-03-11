from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from qanorm.stage2a.agents.answering import Composer, ComposerResult, GroundingVerifier
from qanorm.stage2a.contracts import AnswerClaimDTO, EvidenceItemDTO
from qanorm.stage2a.providers import Stage2ADspyModelBundle


def _build_bundle() -> Stage2ADspyModelBundle:
    placeholder = object()
    return Stage2ADspyModelBundle(
        provider_name="gemini",
        controller=placeholder,
        composer=placeholder,
        verifier=placeholder,
        reranker=placeholder,
    )


def _build_evidence() -> list[EvidenceItemDTO]:
    document_id = uuid4()
    version_id = uuid4()
    return [
        EvidenceItemDTO(
            evidence_id="ev-0001",
            source_kind="document_node_locator",
            document_id=document_id,
            document_version_id=version_id,
            node_id=uuid4(),
            retrieval_unit_id=None,
            locator="5.1",
            heading_path="Раздел 5",
            score=1.0,
            text="Высота ограждения должна быть не менее 1,2 м.",
        )
    ]


def test_composer_filters_claims_to_available_evidence() -> None:
    evidence = _build_evidence()

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                assert "ev-0001" in kwargs["evidence_bundle"]
                return SimpleNamespace(
                    answer_text="Минимальная высота ограждения составляет 1,2 м [ev-0001].",
                    claims_json='[{"text":"Высота не менее 1,2 м.","evidence_ids":["ev-0001"]},'
                    '{"text":"Неподтвержденный тезис.","evidence_ids":["ev-9999"]}]',
                    limitations_json='["Нужен контекст типа здания."]',
                )

        return _Program()

    composer = Composer(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    draft = composer.compose(
        query_text="Какая минимальная высота ограждения?",
        answer_mode="direct",
        evidence=evidence,
    )

    assert draft.answer_text.startswith("Минимальная высота")
    assert len(draft.claims) == 1
    assert draft.claims[0].evidence_ids == ["ev-0001"]
    assert draft.limitations == ["Нужен контекст типа здания."]


def test_grounding_verifier_filters_unsupported_claims_and_downgrades_mode() -> None:
    evidence = _build_evidence()
    draft = ComposerResult(
        answer_mode="direct",
        answer_text="Черновик ответа [ev-0001].",
        claims=[
            AnswerClaimDTO(text="Подтвержденный тезис.", evidence_ids=["ev-0001"]),
            AnswerClaimDTO(text="Неподтвержденный тезис.", evidence_ids=["ev-9999"]),
        ],
        evidence=evidence,
        limitations=["Черновик."],
    )

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                assert "Черновик ответа" in kwargs["answer_text"]
                return SimpleNamespace(
                    verified_answer_text="Проверенный ответ [ev-0001].",
                    supported_claims_json='[{"text":"Подтвержденный тезис.","evidence_ids":["ev-0001"]},'
                    '{"text":"Лишний тезис.","evidence_ids":["ev-9999"]}]',
                    limitations_json='["После верификации оставлен только подтвержденный тезис."]',
                    final_mode="direct",
                )

        return _Program()

    verifier = GroundingVerifier(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    answer = verifier.verify(
        query_text="Какая минимальная высота ограждения?",
        draft=draft,
    )

    assert answer.mode == "partial"
    assert answer.answer_text == "Проверенный ответ [ev-0001]."
    assert len(answer.claims) == 1
    assert len(answer.evidence) == 1
    assert "После верификации" in answer.limitations[1]


def test_grounding_verifier_returns_no_answer_when_no_supported_claims() -> None:
    evidence = _build_evidence()
    draft = ComposerResult(
        answer_mode="partial",
        answer_text="Сомнительный черновик.",
        claims=[AnswerClaimDTO(text="Сомнительный тезис.", evidence_ids=["ev-9999"])],
        evidence=evidence,
    )

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                return SimpleNamespace(
                    verified_answer_text="",
                    supported_claims_json="[]",
                    limitations_json="[]",
                    final_mode="partial",
                )

        return _Program()

    verifier = GroundingVerifier(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    answer = verifier.verify(
        query_text="Какая минимальная высота ограждения?",
        draft=draft,
    )

    assert answer.mode == "partial"
    assert answer.claims == []
    assert answer.evidence == []
    assert "подтвержденные claims не найдены" in answer.limitations[0]
