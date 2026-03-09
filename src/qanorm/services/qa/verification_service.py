"""Verification, safety, and bounded repair-loop services for Stage 2 answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.agents.answer_synthesizer import StructuredAnswer
from qanorm.db.types import QueryStatus, VerificationResult
from qanorm.models import VerificationReport
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import PromptRegistry, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import ChatMessage, ChatModelProvider, ChatRequest, create_role_bound_providers
from qanorm.repositories import QAAnswerRepository, QAQueryRepository, VerificationReportRepository
from qanorm.security.guards import (
    SecurityDecision,
    SecurityFinding,
    SessionIsolationGuard,
    enforce_tool_call_budget,
    inspect_retrieved_content,
    inspect_user_input,
    record_security_findings,
)
from qanorm.settings import RuntimeConfig, get_settings
from qanorm.utils.text import normalize_whitespace


JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
QUESTION_SPLIT_RE = re.compile(r"(?:,|;|\?| и | or )", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{3,}", re.UNICODE)


@dataclass(slots=True, frozen=True)
class VerificationFinding:
    """One verification-layer finding emitted by auditors or guards."""

    kind: str
    result: VerificationResult
    message: str
    repairable: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class VerificationOutcome:
    """Aggregated verification output for one answer attempt."""

    coverage_result: VerificationResult
    citation_result: VerificationResult
    hallucination_result: VerificationResult
    source_labeling_result: VerificationResult
    findings: list[VerificationFinding]
    security_findings: list[SecurityFinding]

    @property
    def has_blocking_failures(self) -> bool:
        """Return whether the answer cannot be accepted as-is."""

        verification_failed = any(item.result == VerificationResult.FAIL for item in self.findings)
        security_blocked = any(item.blocks_execution for item in self.security_findings)
        return verification_failed or security_blocked

    @property
    def repairable_findings(self) -> list[VerificationFinding]:
        """Return only findings that a repair pass may still address."""

        return [item for item in self.findings if item.repairable]

    @property
    def warnings_payload(self) -> list[dict[str, Any]]:
        """Serialize findings into a DB-friendly structure."""

        return [
            {"kind": item.kind, "result": item.result.value, "message": item.message, "repairable": item.repairable, "details": item.details}
            for item in self.findings
        ] + [
            {
                "kind": "security",
                "result": "fail" if item.blocks_execution else "warning",
                "message": item.message,
                "repairable": False,
                "details": item.details | {"event_type": item.event_type, "severity": item.severity.value},
            }
            for item in self.security_findings
        ]


RepairCallback = Callable[[StructuredAnswer, list[VerificationFinding]], Awaitable[StructuredAnswer]]


class VerificationService:
    """Run hybrid verification and bounded repair loops for synthesized answers."""

    def __init__(
        self,
        session: Session,
        *,
        runtime_config: RuntimeConfig | None = None,
        prompt_registry: PromptRegistry | None = None,
        provider: ChatModelProvider | None = None,
        report_repository: VerificationReportRepository | None = None,
        answer_repository: QAAnswerRepository | None = None,
        query_repository: QAQueryRepository | None = None,
        session_isolation_guard: SessionIsolationGuard | None = None,
    ) -> None:
        self.session = session
        self.runtime_config = runtime_config or get_settings()
        self.prompt_registry = prompt_registry or create_prompt_registry(self.runtime_config)
        if provider is None:
            provider = create_role_bound_providers(
                registry=create_provider_registry(),
                runtime_config=self.runtime_config,
            ).synthesis
        self.provider = provider
        self.report_repository = report_repository or VerificationReportRepository(session)
        self.answer_repository = answer_repository or QAAnswerRepository(session)
        self.query_repository = query_repository or QAQueryRepository(session)
        self.session_isolation_guard = session_isolation_guard or SessionIsolationGuard()

    async def verify_answer(
        self,
        *,
        state: QueryState,
        answer: StructuredAnswer,
    ) -> VerificationOutcome:
        """Run hybrid verification against one synthesized answer."""

        citation_findings = await self._audit_citations(state=state, answer=answer)
        coverage_findings = await self._audit_coverage(state=state, answer=answer)
        hallucination_findings = await self._audit_hallucinations(state=state, answer=answer)
        source_labeling_findings = self._validate_source_labeling(answer)
        security_findings = self._run_security_checks(state=state, answer=answer)

        findings = [
            *citation_findings,
            *coverage_findings,
            *hallucination_findings,
            *source_labeling_findings,
        ]
        outcome = VerificationOutcome(
            coverage_result=_max_result(coverage_findings),
            citation_result=_max_result(citation_findings),
            hallucination_result=_max_result(hallucination_findings),
            source_labeling_result=_max_result(source_labeling_findings),
            findings=findings,
            security_findings=security_findings,
        )
        self._persist_outcome(state=state, outcome=outcome)
        return outcome

    async def run_bounded_repair_loop(
        self,
        *,
        state: QueryState,
        initial_answer: StructuredAnswer,
        repair_callback: RepairCallback,
        max_verification_retries: int = 2,
        max_total_attempts: int = 3,
        max_tool_calls: int = 12,
        max_time_budget_seconds: int = 30,
    ) -> tuple[StructuredAnswer, VerificationOutcome]:
        """Run a bounded repair loop until the answer passes or degrades honestly."""

        state.attempt_deadline = datetime.now(timezone.utc) + timedelta(seconds=max_time_budget_seconds)
        current_answer = initial_answer
        best_outcome: VerificationOutcome | None = None

        for attempt_index in range(max_total_attempts):
            state.status = QueryStatus.VERIFYING
            outcome = await self.verify_answer(state=state, answer=current_answer)
            best_outcome = outcome
            previous_findings_fingerprint = state.verification_fingerprint
            previous_evidence_fingerprint = state.evidence_fingerprint
            findings_fingerprint = state.refresh_verification_fingerprint(item.message for item in outcome.findings)
            evidence_fingerprint = state.refresh_evidence_fingerprint()

            if not outcome.has_blocking_failures and not outcome.repairable_findings:
                return current_answer, outcome
            if self._should_stop_repairs(
                state=state,
                outcome=outcome,
                max_verification_retries=max_verification_retries,
                max_total_attempts=max_total_attempts,
                max_tool_calls=max_tool_calls,
                evidence_fingerprint=evidence_fingerprint,
                findings_fingerprint=findings_fingerprint,
                previous_evidence_fingerprint=previous_evidence_fingerprint,
                previous_findings_fingerprint=previous_findings_fingerprint,
                attempt_index=attempt_index,
            ):
                return self._degrade_answer(current_answer, outcome), outcome

            state.repair_attempt_count += 1
            current_answer = await repair_callback(current_answer, outcome.repairable_findings)

        assert best_outcome is not None
        return self._degrade_answer(current_answer, best_outcome), best_outcome

    async def _audit_citations(self, *, state: QueryState, answer: StructuredAnswer) -> list[VerificationFinding]:
        """Audit presence and shape of citations for normative sections."""

        findings: list[VerificationFinding] = []
        for section in answer.sections:
            if section.source_kind.value == "normative" and not section.citations:
                findings.append(
                    VerificationFinding(
                        kind="citation",
                        result=VerificationResult.FAIL,
                        message=f"Normative section '{section.heading}' has no citations.",
                        repairable=True,
                    )
                )
            for citation in section.citations:
                if citation.is_normative and not (citation.locator or citation.quote):
                    findings.append(
                        VerificationFinding(
                            kind="citation",
                            result=VerificationResult.WARNING,
                            message=f"Normative citation '{citation.title}' lacks locator or quote.",
                            repairable=True,
                        )
                    )
        findings.extend(await self._model_findings(prompt_name="citation_auditor", state=state, answer=answer))
        return findings

    async def _audit_coverage(self, *, state: QueryState, answer: StructuredAnswer) -> list[VerificationFinding]:
        """Estimate whether the answer covers the main aspects of the question."""

        aspects = [normalize_whitespace(item) for item in QUESTION_SPLIT_RE.split(state.query_text) if normalize_whitespace(item)]
        haystack = normalize_whitespace(" ".join(section.body for section in answer.sections)).casefold()
        findings: list[VerificationFinding] = []
        uncovered = [aspect for aspect in aspects if not any(token in haystack for token in _extract_tokens(aspect))]
        if uncovered:
            findings.append(
                VerificationFinding(
                    kind="coverage",
                    result=VerificationResult.WARNING,
                    message=f"Answer may not cover all query aspects: {', '.join(uncovered[:3])}.",
                    repairable=True,
                    details={"uncovered_aspects": uncovered[:5]},
                )
            )
        findings.extend(await self._model_findings(prompt_name="coverage_auditor", state=state, answer=answer))
        return findings

    async def _audit_hallucinations(self, *, state: QueryState, answer: StructuredAnswer) -> list[VerificationFinding]:
        """Check whether answer sentences are grounded in the collected evidence."""

        evidence_tokens = set().union(*(_extract_tokens(item.quote or item.chunk_text or "") for item in state.evidence_bundle.all_items))
        findings: list[VerificationFinding] = []
        for sentence in SENTENCE_SPLIT_RE.split(answer.answer_text):
            sentence = normalize_whitespace(sentence)
            if len(sentence) < 20:
                continue
            sentence_tokens = _extract_tokens(sentence)
            if not sentence_tokens:
                continue
            overlap_ratio = len(sentence_tokens & evidence_tokens) / max(1, len(sentence_tokens))
            if overlap_ratio < 0.2:
                findings.append(
                    VerificationFinding(
                        kind="hallucination",
                        result=VerificationResult.FAIL,
                        message=f"Answer sentence may be unsupported: {sentence[:140]}",
                        repairable=False,
                        details={"overlap_ratio": round(overlap_ratio, 3)},
                    )
                )
        findings.extend(await self._model_findings(prompt_name="hallucination_guard", state=state, answer=answer))
        return findings

    def _validate_source_labeling(self, answer: StructuredAnswer) -> list[VerificationFinding]:
        """Check that source provenance labels remain internally consistent."""

        findings: list[VerificationFinding] = []
        for section in answer.sections:
            is_external_section = section.source_kind.value in {"trusted_web", "open_web"}
            for citation in section.citations:
                if citation.is_normative and citation.requires_verification:
                    findings.append(
                        VerificationFinding(
                            kind="source_labeling",
                            result=VerificationResult.WARNING,
                            message=f"Normative citation '{citation.title}' is incorrectly marked as requiring verification.",
                            repairable=True,
                        )
                    )
                if is_external_section and not citation.requires_verification:
                    findings.append(
                        VerificationFinding(
                            kind="source_labeling",
                            result=VerificationResult.FAIL,
                            message=f"External citation '{citation.title}' must require verification.",
                            repairable=True,
                        )
                    )
        return findings

    def _run_security_checks(self, *, state: QueryState, answer: StructuredAnswer) -> list[SecurityFinding]:
        """Run safety guards over user input, evidence, tool usage, and session isolation."""

        findings: list[SecurityFinding] = []
        findings.extend(inspect_user_input(state.query_text).findings)
        for evidence in state.evidence_bundle.trusted_web + state.evidence_bundle.open_web:
            findings.extend(
                inspect_retrieved_content(
                    evidence.quote or evidence.chunk_text or "",
                    source_kind=evidence.source_kind.value,
                ).findings
            )
        findings.extend(enforce_tool_call_budget(state, max_tool_calls=12).findings)

        cache_key = self.session_isolation_guard.build_cache_key(state.session_id, "verification")
        self.session_isolation_guard.assert_cache_key(session_id=state.session_id, cache_key=cache_key)
        self.session_isolation_guard.assert_worker_payload(
            session_id=state.session_id,
            payload={"session_id": str(state.session_id), "query_id": str(state.query_id or "")},
        )
        self.session_isolation_guard.assert_temp_artifact_path(
            session_id=state.session_id,
            path=f"data/temp/{state.session_id}/verification.json",
        )
        return findings

    async def _model_findings(
        self,
        *,
        prompt_name: str,
        state: QueryState,
        answer: StructuredAnswer,
    ) -> list[VerificationFinding]:
        """Ask the configured model for additional structured verification findings."""

        prompt = self.prompt_registry.render(prompt_name, context=state.build_prompt_context())
        schema = {
            "findings": [
                {
                    "kind": prompt_name,
                    "result": "pass|warning|fail",
                    "message": "short issue description",
                    "repairable": True,
                }
            ]
        }
        try:
            response = await self.provider.generate(
                ChatRequest(
                    model=self.provider.model,
                    messages=[
                        ChatMessage(role="system", content=prompt.text),
                        ChatMessage(
                            role="user",
                            content=(
                                "Return only one JSON object using this schema:\n"
                                f"{json.dumps(schema, ensure_ascii=False)}\n\n"
                                f"Query:\n{state.query_text}\n\n"
                                f"Answer markdown:\n{answer.markdown}\n"
                            ),
                        ),
                    ],
                    temperature=0.0,
                    max_tokens=500,
                    metadata={"prompt_metadata": prompt.metadata},
                )
            )
        except Exception:
            return []

        match = JSON_OBJECT_RE.search(response.content)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        findings: list[VerificationFinding] = []
        for item in payload.get("findings", []):
            try:
                result = VerificationResult(str(item["result"]))
            except (KeyError, ValueError):
                continue
            if result == VerificationResult.PASS:
                continue
            findings.append(
                VerificationFinding(
                    kind=str(item.get("kind", prompt_name)),
                    result=result,
                    message=normalize_whitespace(str(item.get("message", ""))),
                    repairable=bool(item.get("repairable", result != VerificationResult.FAIL)),
                    details={"source": "model_assisted"},
                )
            )
        return findings

    def _persist_outcome(self, *, state: QueryState, outcome: VerificationOutcome) -> VerificationReport:
        """Persist verification/security findings for the current query."""

        report = self.report_repository.add(
            VerificationReport(
                query_id=state.query_id,
                coverage_result=outcome.coverage_result,
                citation_result=outcome.citation_result,
                hallucination_result=outcome.hallucination_result,
                source_labeling_result=outcome.source_labeling_result,
                warnings_json=outcome.warnings_payload,
            )
        )
        record_security_findings(
            self.session,
            query_id=state.query_id,
            session_id=state.session_id,
            findings=outcome.security_findings,
        )
        return report

    def _should_stop_repairs(
        self,
        *,
        state: QueryState,
        outcome: VerificationOutcome,
        max_verification_retries: int,
        max_total_attempts: int,
        max_tool_calls: int,
        evidence_fingerprint: str,
        findings_fingerprint: str,
        previous_evidence_fingerprint: str | None,
        previous_findings_fingerprint: str | None,
        attempt_index: int,
    ) -> bool:
        """Evaluate all bounded repair-loop stop conditions."""

        if not outcome.repairable_findings:
            return True
        if state.repair_attempt_count >= max_verification_retries:
            return True
        if attempt_index + 1 >= max_total_attempts:
            return True
        if state.tool_call_count > max_tool_calls:
            return True
        if state.attempt_deadline is not None and datetime.now(timezone.utc) >= state.attempt_deadline:
            return True
        if previous_evidence_fingerprint == evidence_fingerprint and previous_findings_fingerprint == findings_fingerprint:
            return True
        return False

    def _degrade_answer(self, answer: StructuredAnswer, outcome: VerificationOutcome) -> StructuredAnswer:
        """Return a clearly limited answer when verification cannot be improved further."""

        warnings = list(answer.warnings)
        warnings.append("Ответ ограничен verification layer: часть утверждений требует дополнительной проверки.")
        return StructuredAnswer(
            answer_text=answer.answer_text,
            markdown=answer.markdown + "\n\n### Ограничения verification\n\n- Часть выводов требует ручной проверки.",
            answer_format=answer.answer_format,
            coverage_status=answer.coverage_status,
            has_stale_sources=answer.has_stale_sources,
            has_external_sources=answer.has_external_sources,
            assumptions=list(answer.assumptions),
            limitations=list(answer.limitations),
            warnings=warnings,
            sections=list(answer.sections),
            model_name=answer.model_name,
        )


def _extract_tokens(value: str) -> set[str]:
    """Extract a compact comparable token set from free-form text."""

    return {match.group(0).casefold() for match in TOKEN_RE.finditer(normalize_whitespace(value))}


def _max_result(findings: list[VerificationFinding]) -> VerificationResult:
    """Reduce findings into one overall verification result."""

    if any(item.result == VerificationResult.FAIL for item in findings):
        return VerificationResult.FAIL
    if any(item.result == VerificationResult.WARNING for item in findings):
        return VerificationResult.WARNING
    return VerificationResult.PASS
