"""DSPy answer layer for grounded Stage 2A responses."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal

import dspy
from pydantic import BaseModel, Field

from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import AnswerClaimDTO, EvidenceItemDTO, Stage2AAnswerDTO
from qanorm.stage2a.providers import Stage2ADspyModelBundle, build_stage2a_dspy_models


class ComposerSignature(dspy.Signature):
    """Draft a grounded answer using only the supplied evidence lines and cite evidence ids inline."""

    query_text: str = dspy.InputField(desc="Original user question.")
    answer_mode: str = dspy.InputField(desc="Requested answer mode from the controller.")
    evidence_bundle: str = dspy.InputField(desc="Compact evidence pack with evidence ids.")
    answer_text: str = dspy.OutputField(desc="Grounded draft answer with inline evidence ids such as [ev-0001].")
    claims_json: str = dspy.OutputField(
        desc="JSON array of objects with fields text and evidence_ids. Use only evidence ids present in the bundle."
    )
    limitations_json: str = dspy.OutputField(desc="JSON array of short answer limitations.")


class VerifierSignature(dspy.Signature):
    """Keep only supported claims, downgrade certainty when evidence is weak, and remove unsupported statements."""

    query_text: str = dspy.InputField(desc="Original user question.")
    answer_mode: str = dspy.InputField(desc="Current draft answer mode.")
    answer_text: str = dspy.InputField(desc="Draft answer text with inline evidence ids.")
    claims_json: str = dspy.InputField(desc="Draft claims JSON from the composer.")
    evidence_bundle: str = dspy.InputField(desc="Compact evidence pack with evidence ids.")
    verified_answer_text: str = dspy.OutputField(desc="Verified answer text after removing unsupported statements.")
    supported_claims_json: str = dspy.OutputField(
        desc="JSON array of supported claims with fields text and evidence_ids."
    )
    limitations_json: str = dspy.OutputField(desc="JSON array of short limitations after verification.")
    final_mode: str = dspy.OutputField(desc="One of direct, partial, clarify, or no_answer.")


class ComposerResult(BaseModel):
    """Intermediate composer output before grounding verification."""

    answer_mode: Literal["direct", "partial", "clarify", "no_answer"]
    answer_text: str = Field(min_length=1)
    claims: list[AnswerClaimDTO] = Field(default_factory=list)
    evidence: list[EvidenceItemDTO] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class Composer:
    """Draft a grounded answer from controller-selected evidence."""

    def __init__(
        self,
        *,
        config: Stage2AConfig | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        program_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.models = model_bundle or build_stage2a_dspy_models(self.config)
        self._program_factory = program_factory or self._build_program

    def compose(
        self,
        *,
        query_text: str,
        answer_mode: Literal["direct", "partial", "clarify", "no_answer"],
        evidence: list[EvidenceItemDTO],
    ) -> ComposerResult:
        """Produce one draft answer tied to the supplied evidence pack."""

        program = self._program_factory()
        evidence_bundle = format_evidence_bundle(evidence)
        with dspy.context(lm=self.models.composer):
            prediction = program(
                query_text=query_text,
                answer_mode=answer_mode,
                evidence_bundle=evidence_bundle,
            )

        answer_text = (getattr(prediction, "answer_text", "") or "").strip()
        if not answer_text:
            answer_text = "Недостаточно данных для сформулированного ответа."
        claims = _normalize_claims(
            _parse_claims_json(getattr(prediction, "claims_json", ""), available_evidence=evidence),
            available_evidence=evidence,
        )
        if not claims and evidence:
            claims = [AnswerClaimDTO(text=answer_text, evidence_ids=[item.evidence_id for item in evidence])]
        limitations = _parse_string_list_json(getattr(prediction, "limitations_json", ""))

        return ComposerResult(
            answer_mode=answer_mode,
            answer_text=answer_text,
            claims=claims,
            evidence=evidence,
            limitations=limitations,
        )

    def _build_program(self) -> Any:
        return dspy.ChainOfThought(ComposerSignature)


class GroundingVerifier:
    """Filter unsupported claims and finalize the grounded answer object."""

    def __init__(
        self,
        *,
        config: Stage2AConfig | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        program_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.models = model_bundle or build_stage2a_dspy_models(self.config)
        self._program_factory = program_factory or self._build_program

    def verify(self, *, query_text: str, draft: ComposerResult) -> Stage2AAnswerDTO:
        """Run model verification and deterministic evidence filtering."""

        program = self._program_factory()
        evidence_bundle = format_evidence_bundle(draft.evidence)
        claims_json = json.dumps([claim.model_dump() for claim in draft.claims], ensure_ascii=False)
        with dspy.context(lm=self.models.verifier):
            prediction = program(
                query_text=query_text,
                answer_mode=draft.answer_mode,
                answer_text=draft.answer_text,
                claims_json=claims_json,
                evidence_bundle=evidence_bundle,
            )

        supported_claims = _normalize_claims(
            _parse_claims_json(getattr(prediction, "supported_claims_json", ""), available_evidence=draft.evidence),
            available_evidence=draft.evidence,
        )
        supported_claims = [claim for claim in supported_claims if claim.supported]
        final_mode = _normalize_answer_mode(getattr(prediction, "final_mode", draft.answer_mode))

        if not supported_claims:
            final_mode = "partial" if draft.evidence else "no_answer"
            limitations = draft.limitations + ["Ответ был автоматически ограничен: подтвержденные claims не найдены."]
            verified_answer_text = "Недостаточно подтвержденных данных для уверенного ответа."
            return Stage2AAnswerDTO(
                mode=final_mode,
                answer_text=verified_answer_text,
                claims=[],
                evidence=[],
                limitations=_dedupe_preserve_order(limitations),
            )

        supported_ids = {evidence_id for claim in supported_claims for evidence_id in claim.evidence_ids}
        filtered_evidence = [item for item in draft.evidence if item.evidence_id in supported_ids]
        verified_answer_text = (getattr(prediction, "verified_answer_text", "") or "").strip()
        if not verified_answer_text:
            verified_answer_text = " ".join(claim.text for claim in supported_claims)
        limitations = _dedupe_preserve_order(
            draft.limitations + _parse_string_list_json(getattr(prediction, "limitations_json", ""))
        )

        if final_mode == "direct" and len(filtered_evidence) < self.config.retrieval.min_direct_answer_evidence:
            final_mode = "partial"

        return Stage2AAnswerDTO(
            mode=final_mode,
            answer_text=verified_answer_text,
            claims=supported_claims,
            evidence=filtered_evidence,
            limitations=limitations,
        )

    def _build_program(self) -> Any:
        return dspy.ChainOfThought(VerifierSignature)


def format_evidence_bundle(evidence: list[EvidenceItemDTO]) -> str:
    """Format evidence into a compact prompt-friendly bundle."""

    if not evidence:
        return "No evidence available."
    lines: list[str] = []
    for item in evidence:
        lines.append(
            f"{item.evidence_id} | locator={item.locator or '-'} | heading={item.heading_path or '-'} | "
            f"source={item.source_kind} | text={_truncate_text(item.text)}"
        )
    return "\n".join(lines)


def _parse_claims_json(raw_value: str, *, available_evidence: list[EvidenceItemDTO]) -> list[AnswerClaimDTO]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    evidence_ids = {item.evidence_id for item in available_evidence}
    claims: list[AnswerClaimDTO] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        raw_ids = item.get("evidence_ids") or []
        if not text or not isinstance(raw_ids, list):
            continue
        filtered_ids = [str(value) for value in raw_ids if str(value) in evidence_ids]
        claims.append(
            AnswerClaimDTO(
                text=text,
                evidence_ids=filtered_ids,
                supported=bool(filtered_ids),
            )
        )
    return claims


def _parse_string_list_json(raw_value: str) -> list[str]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    items: list[str] = []
    for item in payload:
        value = str(item).strip()
        if value:
            items.append(value)
    return items


def _normalize_claims(
    claims: list[AnswerClaimDTO],
    *,
    available_evidence: list[EvidenceItemDTO],
) -> list[AnswerClaimDTO]:
    allowed_ids = {item.evidence_id for item in available_evidence}
    normalized: list[AnswerClaimDTO] = []
    for claim in claims:
        evidence_ids = [evidence_id for evidence_id in claim.evidence_ids if evidence_id in allowed_ids]
        if not evidence_ids:
            continue
        normalized.append(claim.model_copy(update={"evidence_ids": evidence_ids, "supported": True}))
    return normalized


def _normalize_answer_mode(raw_value: str) -> Literal["direct", "partial", "clarify", "no_answer"]:
    value = (raw_value or "").strip().casefold()
    if value in {"direct", "partial", "clarify", "no_answer"}:
        return value  # type: ignore[return-value]
    return "partial"


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _truncate_text(text: str, *, limit: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."
