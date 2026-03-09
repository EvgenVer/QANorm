"""Evidence-driven answer synthesis for Stage 2."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from qanorm.audit import AuditWriter
from qanorm.db.types import AnswerStatus, CoverageStatus, EvidenceSourceKind, FreshnessStatus, MessageRole, QueryStatus
from qanorm.models import QAAnswer, QAEvidence, QAMessage, QAQuery
from qanorm.models.qa_state import EvidenceBundle, QueryState
from qanorm.prompts.registry import PromptRegistry, create_prompt_registry
from qanorm.providers import create_provider_registry
from qanorm.providers.base import ChatMessage, ChatModelProvider, ChatRequest, create_role_bound_providers
from qanorm.repositories import QAAnswerRepository, QAMessageRepository, QAQueryRepository
from qanorm.settings import RuntimeConfig, get_settings
from qanorm.utils.text import normalize_whitespace


JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True, frozen=True)
class AnswerCitation:
    """One normalized citation shown in the final answer."""

    title: str
    edition_label: str | None
    locator: str | None
    quote: str | None
    is_normative: bool
    requires_verification: bool

    def render(self) -> str:
        """Render the citation into a stable markdown bullet."""

        prefix = "Нормативный источник" if self.is_normative else "Ненормативный источник"
        verification = " Требует проверки." if self.requires_verification else ""
        locator = self.locator or "n/a"
        edition = f", редакция: {self.edition_label}" if self.edition_label else ""
        quote = f' Цитата: "{self.quote}"' if self.quote else ""
        return f"{prefix}: {self.title}{edition}, локатор: {locator}.{verification}{quote}".strip()


@dataclass(slots=True, frozen=True)
class AnswerSection:
    """Structured answer block rendered into the final response."""

    heading: str
    body: str
    source_kind: EvidenceSourceKind
    citations: list[AnswerCitation] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class StructuredAnswer:
    """Serializable answer produced by the synthesizer."""

    answer_text: str
    markdown: str
    answer_format: str
    coverage_status: CoverageStatus
    has_stale_sources: bool
    has_external_sources: bool
    assumptions: list[str]
    limitations: list[str]
    warnings: list[str]
    sections: list[AnswerSection]
    model_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Expose an API-friendly payload for persistence and transport."""

        return {
            "answer_text": self.answer_text,
            "markdown": self.markdown,
            "answer_format": self.answer_format,
            "coverage_status": self.coverage_status.value,
            "has_stale_sources": self.has_stale_sources,
            "has_external_sources": self.has_external_sources,
            "assumptions": list(self.assumptions),
            "limitations": list(self.limitations),
            "warnings": list(self.warnings),
            "sections": [
                {
                    "heading": section.heading,
                    "body": section.body,
                    "source_kind": section.source_kind.value,
                    "citations": [asdict(citation) for citation in section.citations],
                }
                for section in self.sections
            ],
            "model_name": self.model_name,
        }


class AnswerSynthesizer:
    """Synthesize one evidence-based answer and persist it when needed."""

    def __init__(
        self,
        session: Session,
        *,
        runtime_config: RuntimeConfig | None = None,
        prompt_registry: PromptRegistry | None = None,
        provider: ChatModelProvider | None = None,
        answer_repository: QAAnswerRepository | None = None,
        message_repository: QAMessageRepository | None = None,
        query_repository: QAQueryRepository | None = None,
    ) -> None:
        self.session = session
        self.runtime_config = runtime_config or get_settings()
        self.prompt_registry = prompt_registry or create_prompt_registry(self.runtime_config)
        if provider is None:
            bindings = create_role_bound_providers(
                registry=create_provider_registry(),
                runtime_config=self.runtime_config,
            )
            provider = bindings.synthesis
        self.provider = provider
        self.answer_repository = answer_repository or QAAnswerRepository(session)
        self.message_repository = message_repository or QAMessageRepository(session)
        self.query_repository = query_repository or QAQueryRepository(session)

    async def synthesize(
        self,
        state: QueryState,
        *,
        assumptions: list[str] | None = None,
        limitations: list[str] | None = None,
    ) -> StructuredAnswer:
        """Create a structured answer from the current evidence bundle."""

        assumptions_list = [item.strip() for item in (assumptions or []) if item.strip()]
        limitations_list = [item.strip() for item in (limitations or []) if item.strip()]
        prompt = self.prompt_registry.render("answer_synthesizer", context=state.build_prompt_context())
        response = await self.provider.generate(
            ChatRequest(
                model=self.provider.model,
                messages=[
                    ChatMessage(role="system", content=prompt.text),
                    ChatMessage(
                        role="user",
                        content=self._build_instruction(
                            query_text=state.query_text,
                            evidence_bundle=state.evidence_bundle,
                            assumptions=assumptions_list,
                            limitations=limitations_list,
                        ),
                    ),
                ],
                temperature=0.0,
                max_tokens=1100,
                metadata={"prompt_metadata": prompt.metadata},
            )
        )
        sections = self._parse_sections(response.content)
        if not sections:
            sections = self._fallback_sections(state.evidence_bundle)

        prioritized_sections = self._prioritize_sections(sections)
        warnings = self._build_warnings(state.evidence_bundle, limitations_list)
        coverage_status = self._determine_coverage_status(state.query_text, state.evidence_bundle, limitations_list)
        markdown = self._render_markdown(
            query_text=state.query_text,
            sections=prioritized_sections,
            assumptions=assumptions_list,
            limitations=limitations_list,
            warnings=warnings,
            coverage_status=coverage_status,
        )
        answer_text = "\n\n".join(section.body for section in prioritized_sections).strip()
        return StructuredAnswer(
            answer_text=answer_text,
            markdown=markdown,
            answer_format="markdown",
            coverage_status=coverage_status,
            has_stale_sources=any(item.freshness_status != FreshnessStatus.FRESH for item in state.evidence_bundle.all_items),
            has_external_sources=bool(state.evidence_bundle.trusted_web or state.evidence_bundle.open_web),
            assumptions=assumptions_list,
            limitations=limitations_list,
            warnings=warnings,
            sections=prioritized_sections,
            model_name=self.provider.model,
        )

    def persist_answer(self, *, query: QAQuery, answer: StructuredAnswer) -> tuple[QAAnswer, QAMessage]:
        """Persist the final answer and mirror it into session history."""

        existing = self.answer_repository.get_by_query(query.id)
        answer_row = existing or QAAnswer(query_id=query.id, answer_text="", answer_format=answer.answer_format)
        answer_row.answer_text = answer.markdown
        answer_row.answer_format = answer.answer_format
        answer_row.status = AnswerStatus.COMPLETED
        answer_row.coverage_status = answer.coverage_status
        answer_row.has_stale_sources = answer.has_stale_sources
        answer_row.has_external_sources = answer.has_external_sources
        answer_row.model_name = answer.model_name
        saved_answer = self.answer_repository.save(answer_row)

        assistant_message = self.message_repository.add(
            QAMessage(
                id=uuid4(),
                session_id=query.session_id,
                role=MessageRole.ASSISTANT,
                content=answer.markdown,
                metadata_json=answer.to_payload(),
            )
        )
        self.query_repository.update_state(query, status=QueryStatus.COMPLETED)
        if self.session is not None:
            AuditWriter(self.session).write(
                session_id=query.session_id,
                query_id=query.id,
                event_type="final_answer_persisted",
                actor_kind="answer_synthesizer",
                payload_json={
                    "model_provider": getattr(self.provider, "provider_name", "unknown"),
                    "model_name": answer.model_name,
                    "prompt_template_name": "answer_synthesizer",
                    "prompt_version": self.prompt_registry.resolve_version("answer_synthesizer"),
                    "coverage_status": answer.coverage_status.value,
                },
            )
        return saved_answer, assistant_message

    def _build_instruction(
        self,
        *,
        query_text: str,
        evidence_bundle: EvidenceBundle,
        assumptions: list[str],
        limitations: list[str],
    ) -> str:
        """Constrain the synthesis model to a compact JSON response."""

        schema = {
            "sections": [
                {
                    "heading": "Краткий вывод",
                    "body": "one evidence-grounded paragraph",
                    "source_kind": "normative|trusted_web|open_web",
                }
            ]
        }
        return (
            "Return only one JSON object using this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Query:\n{query_text}\n\n"
            f"Normative evidence count: {len(evidence_bundle.normative)}\n"
            f"Trusted web evidence count: {len(evidence_bundle.trusted_web)}\n"
            f"Open web evidence count: {len(evidence_bundle.open_web)}\n"
            f"Assumptions: {json.dumps(assumptions, ensure_ascii=False)}\n"
            f"Limitations: {json.dumps(limitations, ensure_ascii=False)}"
        )

    def _parse_sections(self, content: str) -> list[AnswerSection]:
        """Parse model output when it respects the JSON contract."""

        match = JSON_OBJECT_RE.search(content)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, list):
            return []

        sections: list[AnswerSection] = []
        for item in raw_sections:
            if not isinstance(item, dict):
                return []
            try:
                source_kind = EvidenceSourceKind(str(item["source_kind"]))
            except ValueError:
                return []
            body = normalize_whitespace(str(item.get("body", "")).strip())
            heading = normalize_whitespace(str(item.get("heading", "")).strip())
            if not heading or not body:
                return []
            sections.append(
                AnswerSection(
                    heading=heading,
                    body=body,
                    source_kind=source_kind,
                    citations=[],
                )
            )
        return sections

    def _fallback_sections(self, evidence_bundle: EvidenceBundle) -> list[AnswerSection]:
        """Build a deterministic answer directly from evidence when the model output is invalid."""

        sections: list[AnswerSection] = []
        if evidence_bundle.normative:
            sections.append(
                AnswerSection(
                    heading="Нормативные выводы",
                    body=self._summarize_evidence(evidence_bundle.normative, prefer_normative=True),
                    source_kind=EvidenceSourceKind.NORMATIVE,
                    citations=self._build_citations(evidence_bundle.normative[:3], is_normative=True),
                )
            )
        if evidence_bundle.trusted_web:
            sections.append(
                AnswerSection(
                    heading="Дополнительные trusted sources",
                    body=self._summarize_evidence(evidence_bundle.trusted_web, prefer_normative=False),
                    source_kind=EvidenceSourceKind.TRUSTED_WEB,
                    citations=self._build_citations(evidence_bundle.trusted_web[:2], is_normative=False),
                )
            )
        if evidence_bundle.open_web:
            sections.append(
                AnswerSection(
                    heading="Дополнительные данные из open web",
                    body=self._summarize_evidence(evidence_bundle.open_web, prefer_normative=False),
                    source_kind=EvidenceSourceKind.OPEN_WEB,
                    citations=self._build_citations(evidence_bundle.open_web[:2], is_normative=False),
                )
            )
        if not sections:
            sections.append(
                AnswerSection(
                    heading="Недостаточно данных",
                    body="Подтвержденных evidence-блоков недостаточно для уверенного ответа.",
                    source_kind=EvidenceSourceKind.NORMATIVE,
                )
            )
        return sections

    def _prioritize_sections(self, sections: list[AnswerSection]) -> list[AnswerSection]:
        """Keep normative output first, then trusted, then open-web material."""

        priority = {
            EvidenceSourceKind.NORMATIVE: 0,
            EvidenceSourceKind.TRUSTED_WEB: 1,
            EvidenceSourceKind.OPEN_WEB: 2,
        }
        return sorted(sections, key=lambda item: (priority[item.source_kind], item.heading))

    def _build_citations(self, evidence_rows: list[QAEvidence], *, is_normative: bool) -> list[AnswerCitation]:
        """Convert evidence rows into display-ready citations."""

        citations = []
        for item in evidence_rows:
            title = self._evidence_title(item)
            citations.append(
                AnswerCitation(
                    title=title,
                    edition_label=item.edition_label,
                    locator=item.locator,
                    quote=item.quote,
                    is_normative=is_normative,
                    requires_verification=not is_normative or item.requires_verification,
                )
            )
        return citations

    def _summarize_evidence(self, evidence_rows: list[QAEvidence], *, prefer_normative: bool) -> str:
        """Generate a deterministic section summary from the top evidence rows."""

        lines: list[str] = []
        for item in evidence_rows[:3]:
            title = self._evidence_title(item)
            locator = item.locator or "n/a"
            prefix = "Норма" if prefer_normative else "Источник"
            quote = normalize_whitespace((item.quote or item.chunk_text or "")[:280])
            lines.append(f"{prefix} {title} [{locator}]: {quote}".strip())
        return "\n".join(lines) if lines else "Подтвержденные evidence-блоки отсутствуют."

    def _build_warnings(self, evidence_bundle: EvidenceBundle, limitations: list[str]) -> list[str]:
        """Collect stale-source and external-source warnings for the final answer."""

        warnings: list[str] = []
        if any(item.freshness_status != FreshnessStatus.FRESH for item in evidence_bundle.all_items):
            warnings.append("В ответе использованы источники с неполностью подтвержденной актуальностью.")
        if evidence_bundle.trusted_web or evidence_bundle.open_web:
            warnings.append("Ненормативные источники помечены отдельно и требуют пользовательской проверки.")
        if limitations:
            warnings.append("Ответ содержит ограничения по покрытию вопроса; см. раздел ограничений.")
        return warnings

    def _determine_coverage_status(
        self,
        query_text: str,
        evidence_bundle: EvidenceBundle,
        limitations: list[str],
    ) -> CoverageStatus:
        """Estimate answer coverage from question shape and evidence availability."""

        if not evidence_bundle.all_items:
            return CoverageStatus.INSUFFICIENT
        aspect_count = max(1, len([item for item in re.split(r"(?:,|\bи\b|\?|;)", query_text) if item.strip()]))
        if limitations or len(evidence_bundle.all_items) < min(2, aspect_count):
            return CoverageStatus.PARTIAL
        return CoverageStatus.COMPLETE

    def _render_markdown(
        self,
        *,
        query_text: str,
        sections: list[AnswerSection],
        assumptions: list[str],
        limitations: list[str],
        warnings: list[str],
        coverage_status: CoverageStatus,
    ) -> str:
        """Render the structured answer into one markdown document."""

        parts = [f"## Ответ\n\nЗапрос: {query_text}"]
        for section in sections:
            parts.append(f"### {section.heading}\n\n{section.body}")
            if section.citations:
                parts.append(
                    "\n".join(
                        [f"- {citation.render()}" for citation in section.citations]
                    )
                )
        if assumptions:
            parts.append("### Допущения\n\n" + "\n".join(f"- {item}" for item in assumptions))
        if limitations:
            parts.append("### Ограничения\n\n" + "\n".join(f"- {item}" for item in limitations))
        if warnings:
            parts.append("### Предупреждения\n\n" + "\n".join(f"- {item}" for item in warnings))
        parts.append(f"### Покрытие\n\nСтатус покрытия: `{coverage_status.value}`.")
        return "\n\n".join(parts).strip()

    def _evidence_title(self, item: QAEvidence) -> str:
        """Build a readable title for one evidence row."""

        if item.document is not None:
            return item.document.title or item.document.display_code or item.document.normalized_code or "Нормативный документ"
        if item.source_domain:
            return item.source_domain
        return "Источник"
