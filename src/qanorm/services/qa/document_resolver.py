"""Resolve document hints into one scoped normative retrieval target."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from qanorm.models import Document, DocumentVersion, RetrievalChunk
from qanorm.models.qa_state import QueryState
from qanorm.normalizers.codes import clean_document_code, normalize_document_code


class DocumentResolutionStatus(StrEnum):
    """Possible outcomes of document resolution before retrieval."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(slots=True, frozen=True)
class DocumentResolutionCandidate:
    """One ranked candidate document considered by the resolver."""

    document_id: UUID
    document_version_id: UUID | None
    normalized_code: str
    display_code: str
    title: str | None
    document_type: str | None
    score: float
    matched_on: str
    locator_match_count: int = 0

    def to_payload(self) -> dict[str, object]:
        """Expose a stable payload for state persistence and audit."""

        return {
            "document_id": str(self.document_id),
            "document_version_id": str(self.document_version_id) if self.document_version_id else None,
            "normalized_code": self.normalized_code,
            "display_code": self.display_code,
            "title": self.title,
            "document_type": self.document_type,
            "score": self.score,
            "matched_on": self.matched_on,
            "locator_match_count": self.locator_match_count,
        }


@dataclass(slots=True, frozen=True)
class DocumentResolutionResult:
    """Final resolution output consumed by scoped retrieval."""

    status: DocumentResolutionStatus
    retrieval_scope: str
    matched_hint: str | None = None
    locator_hint: str | None = None
    primary_candidate: DocumentResolutionCandidate | None = None
    candidates: list[DocumentResolutionCandidate] = field(default_factory=list)
    reason: str | None = None

    @property
    def resolved_document_ids(self) -> list[UUID]:
        """Return the document ids that can scope retrieval immediately."""

        if self.primary_candidate is None or self.status is not DocumentResolutionStatus.RESOLVED:
            return []
        return [self.primary_candidate.document_id]

    def to_payload(self) -> dict[str, object]:
        """Serialize resolution metadata for query state and audit."""

        return {
            "status": self.status.value,
            "retrieval_scope": self.retrieval_scope,
            "matched_hint": self.matched_hint,
            "locator_hint": self.locator_hint,
            "reason": self.reason,
            "primary_candidate": self.primary_candidate.to_payload() if self.primary_candidate else None,
            "candidates": [candidate.to_payload() for candidate in self.candidates],
        }


class DocumentResolver:
    """Resolve explicit or implicit document references before retrieval."""

    AMBIGUITY_DELTA = 0.08

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(self, state: QueryState) -> DocumentResolutionResult:
        """Resolve the most likely document from query hints and locators."""

        locator_hint = state.locator_hints[0] if state.locator_hints else None
        document_hints = [hint for hint in state.document_hints if hint.strip()]
        if not document_hints:
            return DocumentResolutionResult(
                status=DocumentResolutionStatus.UNRESOLVED,
                retrieval_scope="global",
                locator_hint=locator_hint,
                reason="no_document_hints",
            )

        candidates = self._find_candidates(document_hints=document_hints, locator_hint=locator_hint)
        if not candidates:
            return DocumentResolutionResult(
                status=DocumentResolutionStatus.UNRESOLVED,
                retrieval_scope="global",
                matched_hint=document_hints[0],
                locator_hint=locator_hint,
                reason="no_candidates_found",
            )

        primary = candidates[0]
        if len(candidates) > 1 and (primary.score - candidates[1].score) < self.AMBIGUITY_DELTA:
            return DocumentResolutionResult(
                status=DocumentResolutionStatus.AMBIGUOUS,
                retrieval_scope="global",
                matched_hint=document_hints[0],
                locator_hint=locator_hint,
                primary_candidate=primary,
                candidates=candidates[:5],
                reason="multiple_candidates_with_close_scores",
            )

        return DocumentResolutionResult(
            status=DocumentResolutionStatus.RESOLVED,
            retrieval_scope="document_scoped",
            matched_hint=document_hints[0],
            locator_hint=locator_hint,
            primary_candidate=primary,
            candidates=candidates[:5],
            reason="resolved_from_document_hints",
        )

    def _find_candidates(
        self,
        *,
        document_hints: list[str],
        locator_hint: str | None,
    ) -> list[DocumentResolutionCandidate]:
        """Load and score document candidates for all normalized hint variants."""

        candidates_by_id: dict[UUID, DocumentResolutionCandidate] = {}
        for hint in document_hints:
            for variant in expand_document_code_variants(hint):
                rows = self.session.execute(self._build_candidate_query(variant)).all()
                for document, version in rows:
                    candidate = self._score_candidate(
                        document=document,
                        version=version,
                        hint=variant,
                        locator_hint=locator_hint,
                    )
                    existing = candidates_by_id.get(candidate.document_id)
                    if existing is None or candidate.score > existing.score:
                        candidates_by_id[candidate.document_id] = candidate
        return sorted(
            candidates_by_id.values(),
            key=lambda item: (-item.score, -item.locator_match_count, item.normalized_code, str(item.document_id)),
        )

    def _build_candidate_query(self, variant: str) -> Select[tuple[Document, DocumentVersion]]:
        """Search active documents by exact and prefix code matches."""

        prefix_pattern = f"{variant}%"
        contains_pattern = f"%{variant}%"
        return (
            select(Document, DocumentVersion)
            .join(
                DocumentVersion,
                and_(
                    DocumentVersion.document_id == Document.id,
                    DocumentVersion.is_active.is_(True),
                ),
            )
            .where(
                or_(
                    Document.normalized_code == variant,
                    func.upper(Document.display_code) == variant,
                    Document.normalized_code.like(prefix_pattern),
                    func.upper(Document.display_code).like(prefix_pattern),
                    func.upper(Document.display_code).like(contains_pattern),
                )
            )
        )

    def _score_candidate(
        self,
        *,
        document: Document,
        version: DocumentVersion,
        hint: str,
        locator_hint: str | None,
    ) -> DocumentResolutionCandidate:
        """Score one candidate conservatively so ambiguous matches stay ambiguous."""

        normalized_hint = normalize_document_code(hint)
        score = 0.0
        matched_on = "contains"
        normalized_code = normalize_document_code(document.normalized_code)
        display_code = normalize_document_code(document.display_code)
        if normalized_code == normalized_hint:
            score += 1.0
            matched_on = "normalized_code_exact"
        elif display_code == normalized_hint:
            score += 0.95
            matched_on = "display_code_exact"
        elif normalized_code.startswith(normalized_hint):
            score += 0.84
            matched_on = "normalized_code_prefix"
        elif display_code.startswith(normalized_hint):
            score += 0.8
            matched_on = "display_code_prefix"
        else:
            score += 0.6

        if document.document_type and normalized_hint.startswith(document.document_type.upper()):
            score += 0.04

        locator_match_count = self._count_locator_matches(document_id=document.id, locator_hint=locator_hint)
        if locator_match_count > 0:
            # Locator matches break ties in favor of the document that contains the requested clause.
            score += 0.18 + min(locator_match_count, 3) * 0.02
            matched_on = f"{matched_on}+locator"

        return DocumentResolutionCandidate(
            document_id=document.id,
            document_version_id=version.id,
            normalized_code=document.normalized_code,
            display_code=document.display_code,
            title=document.title,
            document_type=document.document_type,
            score=round(score, 4),
            matched_on=matched_on,
            locator_match_count=locator_match_count,
        )

    def _count_locator_matches(self, *, document_id: UUID, locator_hint: str | None) -> int:
        """Count locator matches inside active retrieval chunks for one document."""

        if not locator_hint:
            return 0
        stmt = (
            select(func.count(RetrievalChunk.id))
            .where(
                RetrievalChunk.document_id == document_id,
                RetrievalChunk.is_active.is_(True),
                or_(
                    RetrievalChunk.locator.ilike(f"%{locator_hint}%"),
                    RetrievalChunk.locator_end.ilike(f"%{locator_hint}%"),
                ),
            )
        )
        return int(self.session.execute(stmt).scalar_one() or 0)


def expand_document_code_variants(value: str) -> list[str]:
    """Generate canonical variants for short document references like `СП63` and `ГОСТ 21`."""

    cleaned = clean_document_code(value).upper()
    token = cleaned.split(" ", 1)[0] if cleaned else ""
    remainder = cleaned[len(token) :].strip()
    compact = cleaned.replace(" ", "")
    variants: list[str] = []
    for candidate in (
        cleaned,
        normalize_document_code(cleaned),
        f"{token} {remainder}".strip(),
        compact,
    ):
        raw_variant = clean_document_code(candidate).upper()
        if raw_variant and raw_variant not in variants:
            variants.append(raw_variant)
        normalized = normalize_short_document_reference(candidate)
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants


def normalize_short_document_reference(value: str) -> str:
    """Normalize compact document references into the same shape stored in Stage 1."""

    cleaned = clean_document_code(value).upper()
    for prefix in ("СП", "SP", "ГОСТ", "СНИП", "РД", "СТО", "ВСП", "ISO", "EN"):
        if cleaned.startswith(prefix):
            suffix = cleaned[len(prefix) :].strip()
            if suffix and not suffix.startswith(" "):
                return normalize_document_code(f"{prefix} {suffix}")
    return normalize_document_code(cleaned)
