"""High-level orchestration for one Stage 2A query."""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qanorm.db.session import create_session_factory
from qanorm.stage2a.agents import Composer, ControllerAgent, ControllerAgentResult, GroundingVerifier
from qanorm.stage2a.config import Stage2AConfig, get_stage2a_config
from qanorm.stage2a.contracts import AnswerClaimDTO
from qanorm.stage2a.contracts import EvidenceItemDTO, Stage2AAnswerDTO
from qanorm.stage2a.providers import Stage2ADspyModelBundle, build_stage2a_dspy_models
from qanorm.stage2a.retrieval.engine import RetrievalEngine, RetrievalHit


class Stage2AQueryResult(BaseModel):
    """Full answer payload returned by the Stage 2A runtime."""

    controller: ControllerAgentResult
    answer: Stage2AAnswerDTO


class Stage2ARuntime:
    """Compose controller, composer, and verifier into one query workflow."""

    def __init__(
        self,
        *,
        config: Stage2AConfig | None = None,
        session_factory: sessionmaker[Session] | None = None,
        model_bundle: Stage2ADspyModelBundle | None = None,
        controller_factory: Callable[..., ControllerAgent] | None = None,
        composer_factory: Callable[..., Composer] | None = None,
        verifier_factory: Callable[..., GroundingVerifier] | None = None,
    ) -> None:
        self.config = config or get_stage2a_config()
        self.session_factory = session_factory or create_session_factory()
        self.model_bundle = model_bundle or build_stage2a_dspy_models(self.config)
        self._controller_factory = controller_factory or ControllerAgent
        self._composer_factory = composer_factory or Composer
        self._verifier_factory = verifier_factory or GroundingVerifier

    def answer_query(self, query_text: str) -> Stage2AQueryResult:
        """Run the full Stage 2A answer flow for one question."""

        session = self.session_factory()
        try:
            retrieval = RetrievalEngine(session)
            controller = self._controller_factory(
                retrieval_engine=retrieval,
                config=self.config,
                model_bundle=self.model_bundle,
            )
            controller_result = _coerce_controller_result(controller.run(query_text))
            runtime_evidence = _load_runtime_evidence_pack(retrieval, query_text)
            controller_result = _enrich_controller_result(
                controller_result=controller_result,
                runtime_evidence=runtime_evidence,
                config=self.config,
            )

            if not controller_result.evidence:
                answer = Stage2AAnswerDTO(
                    mode=controller_result.answer_mode,
                    answer_text=controller_result.reasoning_summary,
                    claims=[],
                    evidence=[],
                    limitations=["Контроллер не собрал подтвержденные evidence."],
                    debug_trace=_build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace),
                )
                return Stage2AQueryResult(controller=controller_result, answer=answer)

            composer = self._composer_factory(
                config=self.config,
                model_bundle=self.model_bundle,
            )
            draft = composer.compose(
                query_text=query_text,
                answer_mode=controller_result.answer_mode,
                evidence=controller_result.evidence,
            )
            if controller_result.answer_mode == "direct":
                verifier = self._verifier_factory(
                    config=self.config,
                    model_bundle=self.model_bundle,
                )
                answer = verifier.verify(query_text=query_text, draft=draft)
            else:
                answer = _build_interactive_answer_from_draft(draft)
            answer = answer.model_copy(
                update={"debug_trace": _build_debug_trace(controller_result, enabled=self.config.runtime.enable_debug_trace)}
            )
            return Stage2AQueryResult(controller=controller_result, answer=answer)
        finally:
            session.close()


def _build_debug_trace(result: ControllerAgentResult, *, enabled: bool) -> list[str]:
    if not enabled:
        return []
    ordered_keys = sorted(result.trajectory.keys())
    return [f"{key}: {result.trajectory[key]}" for key in ordered_keys]


def _coerce_controller_result(value: ControllerAgentResult | object) -> ControllerAgentResult:
    if isinstance(value, ControllerAgentResult):
        return value
    if hasattr(value, "__dict__"):
        return ControllerAgentResult.model_validate(value.__dict__)
    return ControllerAgentResult.model_validate(value)


def retrieval_hit_to_evidence(hit: RetrievalHit, index: int) -> EvidenceItemDTO:
    return EvidenceItemDTO.from_hit(hit, evidence_id=f"ev-fallback-{index:02d}")


def _load_runtime_evidence_pack(retrieval: RetrievalEngine, query_text: str) -> list[EvidenceItemDTO]:
    if not hasattr(retrieval, "build_evidence_pack"):
        return []
    return [
        retrieval_hit_to_evidence(hit, index)
        for index, hit in enumerate(retrieval.build_evidence_pack(query_text), start=1)
    ]


def _enrich_controller_result(
    *,
    controller_result: ControllerAgentResult,
    runtime_evidence: list[EvidenceItemDTO],
    config: Stage2AConfig,
) -> ControllerAgentResult:
    if not runtime_evidence:
        return controller_result

    controller_quality = _score_evidence_pack(controller_result.evidence)
    runtime_quality = _score_evidence_pack(runtime_evidence)
    should_replace = False
    reason = ""

    if not controller_result.evidence:
        should_replace = True
        reason = "Runtime fallback used the deterministic evidence pack."
    elif _needs_context_enrichment(controller_result.evidence, config=config) and runtime_quality > controller_quality:
        should_replace = True
        reason = "Runtime replaced node-heavy evidence with a more contextual deterministic evidence pack."

    if not should_replace:
        return controller_result

    return controller_result.model_copy(
        update={
            "answer_mode": "partial" if controller_result.answer_mode in {"no_answer", "clarify", "direct"} else controller_result.answer_mode,
            "selected_evidence_ids": [item.evidence_id for item in runtime_evidence],
            "evidence": runtime_evidence,
            "reasoning_summary": f"{controller_result.reasoning_summary} {reason}".strip(),
        }
    )


def _build_interactive_answer_from_draft(draft) -> Stage2AAnswerDTO:
    limitations = list(draft.limitations)
    limitations.append("Verifier skipped for interactive partial answer to avoid degrading the response.")
    return Stage2AAnswerDTO(
        mode=draft.answer_mode,
        answer_text=draft.answer_text,
        claims=_normalize_claims_for_interactive(draft.claims, draft.evidence),
        evidence=draft.evidence,
        limitations=_dedupe_preserve_order(limitations),
    )


def _normalize_claims_for_interactive(
    claims: list[AnswerClaimDTO],
    evidence: list[EvidenceItemDTO],
) -> list[AnswerClaimDTO]:
    allowed_ids = {item.evidence_id for item in evidence}
    normalized: list[AnswerClaimDTO] = []
    for claim in claims:
        evidence_ids = [value for value in claim.evidence_ids if value in allowed_ids]
        if not evidence_ids:
            continue
        normalized.append(claim.model_copy(update={"evidence_ids": evidence_ids, "supported": True}))
    return normalized


def _score_evidence_pack(evidence: list[EvidenceItemDTO]) -> int:
    score = 0
    for item in evidence:
        if item.retrieval_unit_id is not None:
            score += 4
        elif item.node_id is not None:
            score += 1
        if item.locator:
            score += 1
        if item.heading_path:
            score += 1
    return score


def _needs_context_enrichment(evidence: list[EvidenceItemDTO], *, config: Stage2AConfig) -> bool:
    if not evidence:
        return True
    retrieval_unit_count = sum(1 for item in evidence if item.retrieval_unit_id is not None)
    node_count = sum(1 for item in evidence if item.node_id is not None and item.retrieval_unit_id is None)
    if retrieval_unit_count == 0 and node_count > 0:
        return True
    if len(evidence) < config.retrieval.min_direct_answer_evidence:
        return True
    return False


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
