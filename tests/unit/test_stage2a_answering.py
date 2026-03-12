from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from dspy.utils.exceptions import AdapterParseError

from qanorm.stage2a.agents.answering import (
    Composer,
    ComposerResult,
    ComposerSignature,
    GroundingVerifier,
    VerifierSignature,
)
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
            heading_path="Section 5",
            score=1.0,
            text="Minimum guardrail height must be at least 1.2 m.",
        )
    ]


def test_composer_filters_claims_to_available_evidence() -> None:
    evidence = _build_evidence()

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                assert "ev-0001" in kwargs["evidence_bundle"]
                return SimpleNamespace(
                    answer_text="Minimum guardrail height is 1.2 m [ev-0001].",
                    claims_json='[{"text":"Minimum height is 1.2 m.","evidence_ids":["ev-0001"]},'
                    '{"text":"Unsupported claim.","evidence_ids":["ev-9999"]}]',
                    limitations_json='["Building type context may still matter."]',
                )

        return _Program()

    composer = Composer(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    draft = composer.compose(
        query_text="What is the minimum guardrail height?",
        answer_mode="direct",
        evidence=evidence,
    )

    assert draft.answer_text.startswith("Minimum guardrail height")
    assert len(draft.claims) == 1
    assert draft.claims[0].evidence_ids == ["ev-0001"]
    assert draft.limitations == ["Building type context may still matter."]


def test_grounding_verifier_filters_unsupported_claims_and_downgrades_mode() -> None:
    evidence = _build_evidence()
    draft = ComposerResult(
        answer_mode="direct",
        answer_text="Draft answer [ev-0001].",
        claims=[
            AnswerClaimDTO(text="Supported claim.", evidence_ids=["ev-0001"]),
            AnswerClaimDTO(text="Unsupported claim.", evidence_ids=["ev-9999"]),
        ],
        evidence=evidence,
        limitations=["Draft limitation."],
    )

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                assert "Draft answer" in kwargs["answer_text"]
                return SimpleNamespace(
                    verified_answer_text="Verified answer [ev-0001].",
                    supported_claims_json='[{"text":"Supported claim.","evidence_ids":["ev-0001"]},'
                    '{"text":"Extra claim.","evidence_ids":["ev-9999"]}]',
                    limitations_json='["Only one supported claim remained after verification."]',
                    final_mode="direct",
                )

        return _Program()

    verifier = GroundingVerifier(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    answer = verifier.verify(
        query_text="What is the minimum guardrail height?",
        draft=draft,
    )

    assert answer.mode == "partial"
    assert answer.answer_text == "Verified answer [ev-0001]."
    assert len(answer.claims) == 1
    assert len(answer.evidence) == 1
    assert "Only one supported claim remained" in answer.limitations[1]


def test_grounding_verifier_returns_no_answer_when_no_supported_claims() -> None:
    evidence = _build_evidence()
    draft = ComposerResult(
        answer_mode="partial",
        answer_text="Uncertain draft.",
        claims=[AnswerClaimDTO(text="Unsupported claim.", evidence_ids=["ev-9999"])],
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
        query_text="What is the minimum guardrail height?",
        draft=draft,
    )

    assert answer.mode == "partial"
    assert answer.claims == []
    assert len(answer.evidence) == 1
    assert "claims" in answer.limitations[0]


def test_composer_survives_dspy_parse_failure_with_reasoning_only() -> None:
    evidence = _build_evidence()

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                raise AdapterParseError(
                    adapter_name="JSONAdapter",
                    signature=ComposerSignature,
                    lm_response='{"reasoning":"Minimum guardrail height in the located evidence is 1.2 m."}',
                    parsed_result={"reasoning": "Minimum guardrail height in the located evidence is 1.2 m."},
                )

        return _Program()

    composer = Composer(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    draft = composer.compose(
        query_text="What is the minimum guardrail height?",
        answer_mode="direct",
        evidence=evidence,
    )

    assert draft.answer_mode == "partial"
    assert "1.2 m" in draft.answer_text
    assert len(draft.claims) == 1
    assert draft.claims[0].evidence_ids == ["ev-0001"]
    assert any("composer" in limitation for limitation in draft.limitations)


def test_grounding_verifier_survives_dspy_parse_failure() -> None:
    evidence = _build_evidence()
    draft = ComposerResult(
        answer_mode="direct",
        answer_text="Minimum guardrail height is 1.2 m [ev-0001].",
        claims=[AnswerClaimDTO(text="Minimum height is 1.2 m.", evidence_ids=["ev-0001"])],
        evidence=evidence,
        limitations=["Draft limitation."],
    )

    def fake_program_factory():
        class _Program:
            def __call__(self, **kwargs):
                raise AdapterParseError(
                    adapter_name="JSONAdapter",
                    signature=VerifierSignature,
                    lm_response='{"reasoning":"Only the claim about 1.2 m remains supported."}',
                    parsed_result={"reasoning": "Only the claim about 1.2 m remains supported."},
                )

        return _Program()

    verifier = GroundingVerifier(
        model_bundle=_build_bundle(),
        program_factory=fake_program_factory,
    )

    answer = verifier.verify(
        query_text="What is the minimum guardrail height?",
        draft=draft,
    )

    assert answer.mode == "partial"
    assert len(answer.claims) == 1
    assert len(answer.evidence) == 1
    assert "1.2 m" in answer.answer_text
    assert any("verifier" in limitation for limitation in answer.limitations)
