"""Deterministic retrieval engine for Stage 2A hybrid retrieval workflows."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.indexing.fts import build_text_tsv, tokenize_for_fts
from qanorm.models import Document, DocumentNode, RetrievalUnit
from qanorm.normalizers.codes import normalize_document_code
from qanorm.repositories import (
    DocumentAliasRepository,
    DocumentNodeRepository,
    DocumentRepository,
    DocumentVersionRepository,
    RetrievalUnitRepository,
)
from qanorm.stage2a.config import get_stage2a_config
from qanorm.stage2a.indexing.backfill import GeminiEmbeddingClient
from qanorm.stage2a.indexing.aliases import normalize_alias_value
from qanorm.stage2a.retrieval.query_parser import ParsedQuery, QueryParser
from qanorm.utils.text import normalize_whitespace


_YEAR_RE = re.compile(r"(19|20)\d{2}")
_TOPIC_DOCUMENT_PRIORS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("нагруз", "сочетан", "надежн", "предельн", "воздейств"), ("СП 20", "ГОСТ 27751")),
    (("тепло", "утепл", "теплопередач", "огражда", "стен", "конденс"), ("СП 50",)),
    (("эвакуац", "выход", "пожар", "огнестойк", "дым"), ("СП 1", "СП 2", "ФЗ 123")),
    (("фундамент", "основан", "грунт", "свай", "осад"), ("СП 22", "СП 24")),
    (("арматур", "железобет", "бетон", "плит", "колонн"), ("СП 63",)),
)


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    """One candidate canonical document for retrieval."""

    document_id: UUID
    document_version_id: UUID | None
    score: float
    reason: str
    matched_value: str | None
    display_code: str
    title: str | None


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """One retrieval hit from nodes or retrieval units."""

    source_kind: str
    score: float
    document_id: UUID
    document_version_id: UUID
    node_id: UUID | None
    retrieval_unit_id: UUID | None
    order_index: int | None
    locator: str | None
    heading_path: str | None
    text: str
    document_display_code: str | None = None
    document_title: str | None = None


class RetrievalEngine:
    """Hybrid lexical and dense retrieval engine for Stage 2A."""

    def __init__(self, session: Session, *, query_embedding_fn: Callable[[str], list[float]] | None = None) -> None:
        self.session = session
        self.parser = QueryParser()
        self.config = get_stage2a_config()
        self.documents = DocumentRepository(session)
        self.document_versions = DocumentVersionRepository(session)
        self.document_aliases = DocumentAliasRepository(session)
        self.document_nodes = DocumentNodeRepository(session)
        self.retrieval_units = RetrievalUnitRepository(session)
        self._query_embedding_fn = query_embedding_fn

    def parse_query(self, text: str) -> ParsedQuery:
        """Parse one query into deterministic retrieval hints."""

        return self.parser.parse(text)

    def resolve_document(self, query: ParsedQuery) -> list[DocumentCandidate]:
        """Resolve documents from explicit codes or exact aliases."""

        candidates_by_document: dict[UUID, DocumentCandidate] = {}

        for code in query.explicit_document_codes:
            document = self.documents.get_by_normalized_code(code)
            if document is not None:
                self._register_document_candidate(
                    candidates_by_document,
                    document,
                    score=1.15,
                    reason="explicit_code",
                    matched_value=code,
                )

            normalized_alias = normalize_alias_value(code) or code.casefold()
            for alias in self.document_aliases.list_by_alias_normalized(normalized_alias):
                document = self.documents.get(alias.document_id)
                if document is None:
                    continue
                self._register_document_candidate(
                    candidates_by_document,
                    document,
                    score=1.0 + min(alias.confidence, 1.0) * 0.08,
                    reason="exact_alias",
                    matched_value=alias.alias_raw,
                )

            for alias in self.document_aliases.list_by_alias_prefix(normalized_alias):
                document = self.documents.get(alias.document_id)
                if document is None:
                    continue
                self._register_document_candidate(
                    candidates_by_document,
                    document,
                    score=0.82 + min(alias.confidence, 1.0) * 0.08,
                    reason="prefix_alias",
                    matched_value=alias.alias_raw,
                )

        candidates = self._rerank_document_candidates(query, list(candidates_by_document.values()))
        return candidates[: self.config.retrieval.document_shortlist_size]

    def discover_documents(self, query: ParsedQuery) -> list[DocumentCandidate]:
        """Discover likely documents when the query has no explicit code."""

        limit = self.config.retrieval.discover_documents_top_k
        cards = self.retrieval_units.list_all_by_type("document_card")
        if cards:
            lexical_hits = self._rank_retrieval_units(cards, query.lexical_query, source_kind="document_card_lexical")
            dense_hits = self.search_semantic(query.lexical_query, unit_types=["document_card"])
            scored_cards = self.merge_and_rerank_hits(
                locator_hits=[],
                lexical_hits=lexical_hits,
                dense_hits=dense_hits,
                explicit_locator_count=0,
            )
            candidates: list[DocumentCandidate] = []
            seen: set[UUID] = set()
            for hit in scored_cards:
                document = self._load_document_by_version(hit.document_version_id)
                if document is None or document.id in seen:
                    continue
                candidates.append(
                    self._build_document_candidate(
                        document,
                        score=hit.score,
                        reason=hit.source_kind,
                        matched_value=hit.heading_path or hit.locator,
                    )
                )
                seen.add(document.id)
                if len(candidates) >= limit:
                    break
            if candidates:
                return self._rerank_document_candidates(query, candidates)[:limit]

        # Fallback while derived data is still empty: rank raw documents and aliases.
        tokens = set(query.lexical_tokens)
        if not tokens:
            return []

        candidates = []
        for document in self.documents.list_all():
            score = self._score_text(query.lexical_query, _document_fallback_text(document))
            if score <= 0:
                continue
            candidates.append(
                self._build_document_candidate(
                    document,
                    score=score,
                    reason="document_fallback_lexical",
                    matched_value=document.display_code,
                )
            )
        return self._rerank_document_candidates(query, candidates)[:limit]

    def lookup_locator(self, *, document_version_id: UUID, locator: str) -> list[RetrievalHit]:
        """Find node and retrieval-unit hits for one locator inside one document version."""

        locator_normalized = locator.strip()
        unit_hits: list[RetrievalHit] = []
        document = self._load_document_by_version(document_version_id)
        if document is None:
            return []
        for unit in self.retrieval_units.list_for_document_version(document_version_id):
            score = _match_locator_value(locator_normalized, unit.locator_primary, unit.heading_path)
            if score <= 0:
                continue
            unit_hits.append(
                RetrievalHit(
                    source_kind="retrieval_unit_locator" if unit.locator_primary == locator_normalized else "retrieval_unit_locator_context",
                    score=score,
                    document_id=document.id,
                    document_version_id=document_version_id,
                    document_display_code=document.display_code,
                    document_title=document.title,
                    node_id=unit.anchor_node_id,
                    retrieval_unit_id=unit.id,
                    order_index=unit.start_order_index,
                    locator=unit.locator_primary,
                    heading_path=unit.heading_path,
                    text=unit.text,
                )
            )

        node_hits: list[RetrievalHit] = []
        for node in self.document_nodes.list_for_document_version(document_version_id):
            score = _match_locator_value(locator_normalized, node.locator_normalized, node.heading_path)
            if score <= 0:
                continue
            node_hits.append(
                self._build_node_hit(
                    node=node,
                    score=score,
                    source_kind="document_node_locator" if node.locator_normalized == locator_normalized else "document_node_locator_context",
                )
            )

        hits = node_hits + unit_hits
        hits.sort(key=lambda item: (-item.score, item.order_index or 0))
        return hits[: self.config.retrieval.lexical_top_k]

    def search_lexical(
        self,
        query_text: str,
        *,
        document_version_ids: list[UUID] | None = None,
    ) -> list[RetrievalHit]:
        """Run lexical retrieval over scoped retrieval units or nodes."""

        version_ids = document_version_ids or []
        hits: list[RetrievalHit] = []

        if version_ids:
            for version_id in version_ids:
                units = self.retrieval_units.list_for_document_version(version_id)
                if units:
                    hits.extend(self._rank_retrieval_units(units, query_text, source_kind="retrieval_unit_lexical"))
                    continue

                nodes = self.document_nodes.list_for_document_version(version_id)
                hits.extend(self._rank_nodes(nodes, query_text))

        hits.sort(key=lambda item: (-item.score, item.order_index or 0))
        return hits[: self.config.retrieval.lexical_top_k]

    def search_semantic(
        self,
        query_text: str,
        *,
        document_version_ids: list[UUID] | None = None,
        unit_types: list[str] | None = None,
    ) -> list[RetrievalHit]:
        """Run vector retrieval over embedded retrieval units."""

        query_embedding = self._embed_query(query_text)
        if not query_embedding:
            return []

        hits: list[RetrievalHit] = []
        source_kind = "document_card_dense" if unit_types == ["document_card"] else "retrieval_unit_dense"
        results = self.retrieval_units.search_by_vector(
            query_embedding,
            limit=self.config.retrieval.dense_top_k,
            document_version_ids=document_version_ids,
            unit_types=unit_types,
        )
        for unit, distance in results:
            document = self._load_document_by_version(unit.document_version_id)
            if document is None:
                continue
            hits.append(
                RetrievalHit(
                    source_kind=source_kind,
                    score=round(max(0.0, 1.0 - distance), 4),
                    document_id=document.id,
                    document_version_id=unit.document_version_id,
                    document_display_code=document.display_code,
                    document_title=document.title,
                    node_id=unit.anchor_node_id,
                    retrieval_unit_id=unit.id,
                    order_index=unit.start_order_index,
                    locator=unit.locator_primary,
                    heading_path=unit.heading_path,
                    text=unit.text,
                )
            )
        hits.sort(key=lambda item: (-item.score, item.order_index or 0))
        return hits

    def read_node(self, node_id: UUID) -> RetrievalHit | None:
        """Read one node as a retrieval hit."""

        node = self.document_nodes.get(node_id)
        if node is None:
            return None
        return self._build_node_hit(node=node, score=1.0)

    def expand_neighbors(self, *, document_version_id: UUID, node_id: UUID) -> list[RetrievalHit]:
        """Expand neighboring nodes around one anchor node."""

        node = self.document_nodes.get(node_id)
        if node is None:
            return []

        neighbors = self.document_nodes.list_neighbors(
            document_version_id,
            order_index=node.order_index,
            window=self.config.retrieval.neighbor_window,
        )
        return [self._build_node_hit(node=item, score=0.8 if item.id != node_id else 1.0) for item in neighbors]

    def build_evidence_pack(self, query_text: str) -> list[RetrievalHit]:
        """Build a compact evidence pack from resolution, locator lookup, and lexical search."""

        parsed = self.parse_query(query_text)
        documents = self.resolve_document(parsed)
        if not documents:
            documents = self.discover_documents(parsed)

        version_ids = self._scope_document_version_ids(parsed, documents)
        locator_hits: list[RetrievalHit] = []
        for version_id in version_ids:
            for locator in parsed.explicit_locator_values:
                locator_hits.extend(self.lookup_locator(document_version_id=version_id, locator=locator))
        lexical_hits = self.search_lexical(parsed.lexical_query, document_version_ids=version_ids)
        dense_hits = self.search_semantic(
            parsed.lexical_query,
            document_version_ids=version_ids,
            unit_types=["semantic_block"],
        )
        reranked = self.merge_and_rerank_hits(
            locator_hits=locator_hits,
            lexical_hits=lexical_hits,
            dense_hits=dense_hits,
            explicit_locator_count=len(parsed.explicit_locator_values),
        )
        contextual_hits = self._augment_hits_with_local_context(reranked)
        return contextual_hits[: self.config.retrieval.evidence_pack_size]

    def merge_and_rerank_hits(
        self,
        *,
        locator_hits: list[RetrievalHit],
        lexical_hits: list[RetrievalHit],
        dense_hits: list[RetrievalHit] | None = None,
        explicit_locator_count: int,
    ) -> list[RetrievalHit]:
        """Merge hits from different sources and rerank a compact shortlist."""

        merged = _dedupe_hits(locator_hits + lexical_hits + (dense_hits or []))
        scored: list[tuple[float, int, RetrievalHit]] = []
        for hit in merged[: self.config.retrieval.merged_top_k]:
            rerank_score = hit.score
            if hit.source_kind == "retrieval_unit_locator":
                rerank_score += 0.42
            elif hit.source_kind == "retrieval_unit_locator_context":
                rerank_score += 0.30
            elif hit.source_kind == "retrieval_unit_context":
                rerank_score += 0.22
            elif hit.source_kind == "document_node_locator":
                rerank_score += 0.02
            elif hit.source_kind == "document_node_locator_context":
                rerank_score -= 0.02
            elif hit.source_kind == "retrieval_unit_lexical":
                rerank_score += 0.18
            elif hit.source_kind in {"retrieval_unit_dense", "document_card_dense"}:
                rerank_score += 0.15
            elif hit.source_kind == "document_node":
                rerank_score -= 0.04
            if explicit_locator_count and hit.locator:
                rerank_score += 0.05
            scored.append((round(rerank_score, 4), hit.order_index or 0, hit))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[: self.config.retrieval.rerank_top_k]]

    def _build_document_candidate(
        self,
        document: Document,
        *,
        score: float,
        reason: str,
        matched_value: str | None,
    ) -> DocumentCandidate:
        active_version = document.current_version or self.document_versions.get_active_for_document(document.id)
        return DocumentCandidate(
            document_id=document.id,
            document_version_id=active_version.id if active_version is not None else None,
            score=score,
            reason=reason,
            matched_value=matched_value,
            display_code=document.display_code,
            title=document.title,
        )

    def _register_document_candidate(
        self,
        candidates_by_document: dict[UUID, DocumentCandidate],
        document: Document,
        *,
        score: float,
        reason: str,
        matched_value: str | None,
    ) -> None:
        candidate = self._build_document_candidate(
            document,
            score=score,
            reason=reason,
            matched_value=matched_value,
        )
        existing = candidates_by_document.get(document.id)
        if existing is None or candidate.score > existing.score:
            candidates_by_document[document.id] = candidate

    def _rerank_document_candidates(self, query: ParsedQuery, candidates: list[DocumentCandidate]) -> list[DocumentCandidate]:
        """Rerank document candidates using family matching, edition hints, and domain priors."""

        if not candidates:
            return []

        query_requests_legacy = any(code.startswith(("СНИП", "СНиП", "SNIP")) for code in query.explicit_document_codes)
        query_requests_exact_edition = any(_extract_year(code) is not None for code in query.explicit_document_codes)
        topic_priors = _topic_document_priors(query)
        has_modern_candidate = any(not _is_legacy_document(item.display_code, item.title) for item in candidates)
        latest_year_by_family: dict[str, int] = {}
        for candidate in candidates:
            family = _document_family(candidate.display_code)
            year = _extract_year(normalize_document_code(candidate.display_code))
            if year is None:
                continue
            latest_year_by_family[family] = max(latest_year_by_family.get(family, year), year)
        reranked: list[DocumentCandidate] = []
        for candidate in candidates:
            score = candidate.score
            score += self._explicit_code_match_bonus(query, candidate)
            score += self._topic_prior_bonus(candidate, topic_priors)
            version = self.document_versions.get(candidate.document_version_id) if candidate.document_version_id else None
            if version is not None:
                if version.is_active:
                    score += 0.1
                if version.is_outdated:
                    score -= 0.18
            if not query_requests_exact_edition:
                family = _document_family(candidate.display_code)
                candidate_year = _extract_year(normalize_document_code(candidate.display_code))
                latest_year = latest_year_by_family.get(family)
                if latest_year is not None and candidate_year is not None:
                    if candidate_year == latest_year:
                        score += 0.16
                    else:
                        score -= min(0.24, 0.06 * max(1, latest_year - candidate_year))
            if has_modern_candidate and not query_requests_legacy and _is_legacy_document(candidate.display_code, candidate.title):
                score -= 0.22
            reranked.append(
                DocumentCandidate(
                    document_id=candidate.document_id,
                    document_version_id=candidate.document_version_id,
                    score=round(score, 4),
                    reason=candidate.reason,
                    matched_value=candidate.matched_value,
                    display_code=candidate.display_code,
                    title=candidate.title,
                )
            )

        reranked.sort(
            key=lambda item: (
                -item.score,
                0 if not _is_legacy_document(item.display_code, item.title) else 1,
                item.display_code,
            )
        )
        return reranked

    def _explicit_code_match_bonus(self, query: ParsedQuery, candidate: DocumentCandidate) -> float:
        if not query.explicit_document_codes:
            return 0.0
        candidate_code = normalize_document_code(candidate.display_code)
        best_bonus = 0.0
        candidate_year = _extract_year(candidate_code)
        for code in query.explicit_document_codes:
            normalized_code = normalize_document_code(code)
            if candidate_code == normalized_code:
                best_bonus = max(best_bonus, 0.35)
                continue
            if _shares_document_family(candidate_code, normalized_code) and candidate_code.startswith(normalized_code):
                best_bonus = max(best_bonus, 0.22)
            elif _shares_document_family(candidate_code, normalized_code) and normalized_code.startswith(candidate_code):
                best_bonus = max(best_bonus, 0.14)
            elif _shares_document_family(candidate_code, normalized_code):
                best_bonus = max(best_bonus, 0.18)

            query_year = _extract_year(normalized_code)
            if query_year and candidate_year and query_year != candidate_year:
                best_bonus -= 0.28
        return best_bonus

    def _topic_prior_bonus(self, candidate: DocumentCandidate, topic_priors: set[str]) -> float:
        if not topic_priors:
            return 0.0
        for family in topic_priors:
            if _document_matches_family(candidate.display_code, family):
                return 0.18
        return 0.0

    def _load_document_by_version(self, document_version_id: UUID) -> Document | None:
        version = self.document_versions.get(document_version_id)
        if version is None:
            return None
        return self.documents.get(version.document_id)

    def _scope_document_version_ids(self, query: ParsedQuery, documents: list[DocumentCandidate]) -> list[UUID]:
        """Limit retrieval scope for explicit-document queries to the strongest resolved candidates."""

        version_ids = [item.document_version_id for item in documents if item.document_version_id is not None]
        if not query.explicit_document_codes:
            return version_ids
        if not version_ids:
            return []
        return [version_ids[0]]

    def _rank_retrieval_units(self, units: list[RetrievalUnit], query_text: str, *, source_kind: str) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for unit in units:
            score = self._score_text(query_text, unit.text_tsv or unit.text)
            if score <= 0:
                continue
            document = self._load_document_by_version(unit.document_version_id)
            if document is None:
                continue
            hits.append(
                RetrievalHit(
                    source_kind=source_kind,
                    score=score,
                    document_id=document.id,
                    document_version_id=unit.document_version_id,
                    document_display_code=document.display_code,
                    document_title=document.title,
                    node_id=unit.anchor_node_id,
                    retrieval_unit_id=unit.id,
                    order_index=unit.start_order_index,
                    locator=unit.locator_primary,
                    heading_path=unit.heading_path,
                    text=unit.text,
                )
            )
        hits.sort(key=lambda item: (-item.score, item.order_index or 0))
        return hits

    def _rank_nodes(self, nodes: list[DocumentNode], query_text: str) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for node in nodes:
            score = self._score_text(query_text, node.text_tsv or node.text)
            if score <= 0:
                continue
            hits.append(self._build_node_hit(node=node, score=score))
        hits.sort(key=lambda item: (-item.score, item.order_index or 0))
        return hits

    def _build_node_hit(self, *, node: DocumentNode, score: float, source_kind: str = "document_node") -> RetrievalHit:
        document = self._load_document_by_version(node.document_version_id)
        if document is None:
            raise ValueError(f"Document not found for version {node.document_version_id}")
        return RetrievalHit(
            source_kind=source_kind,
            score=score,
            document_id=document.id,
            document_version_id=node.document_version_id,
            document_display_code=document.display_code,
            document_title=document.title,
            node_id=node.id,
            retrieval_unit_id=None,
            order_index=node.order_index,
            locator=node.locator_normalized,
            heading_path=node.heading_path,
            text=node.text,
        )

    def _score_text(self, query_text: str, candidate_text: str) -> float:
        query_tokens = set(tokenize_for_fts(query_text))
        if not query_tokens:
            return 0.0
        candidate_tokens = set((build_text_tsv(normalize_whitespace(candidate_text)) or "").split())
        overlap = query_tokens & candidate_tokens
        if not overlap:
            return 0.0
        return round(len(overlap) / len(query_tokens), 4)

    def _embed_query(self, query_text: str) -> list[float]:
        if self._query_embedding_fn is not None:
            return self._query_embedding_fn(query_text)

        try:
            with GeminiEmbeddingClient(model=self.config.models.embeddings) as client:
                return client.embed_texts([query_text], task_type=self.config.embeddings.query_task_type)[0]
        except Exception:
            return []

    def _augment_hits_with_local_context(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """Expand top node hits with nearby structural context before composing the evidence pack."""

        contextual_hits = list(hits)
        top_expandable = [
            hit
            for hit in hits[: max(1, min(4, self.config.retrieval.evidence_pack_size))]
            if hit.node_id is not None
        ]
        for hit in top_expandable:
            contextual_hits.extend(self._contextual_units_for_node_hit(hit))
            contextual_hits.extend(
                self.expand_neighbors(
                    document_version_id=hit.document_version_id,
                    node_id=hit.node_id,
                )
            )
        reranked = self.merge_and_rerank_hits(
            locator_hits=[
                item
                for item in contextual_hits
                if item.source_kind.endswith("locator") or item.source_kind.endswith("locator_context")
            ],
            lexical_hits=[item for item in contextual_hits if item.source_kind in {"retrieval_unit_lexical", "retrieval_unit_context", "document_node"}],
            dense_hits=[item for item in contextual_hits if item.source_kind in {"retrieval_unit_dense", "document_card_dense"}],
            explicit_locator_count=sum(1 for item in hits if item.locator),
        )
        return _dedupe_hits(reranked)

    def _contextual_units_for_node_hit(self, hit: RetrievalHit) -> list[RetrievalHit]:
        """Load enclosing semantic blocks for one node hit so answer generation can prefer block context."""

        if hit.order_index is None:
            return []
        contextual_hits: list[RetrievalHit] = []
        for unit in self.retrieval_units.list_for_document_version_and_type(hit.document_version_id, "semantic_block"):
            if unit.start_order_index is None or unit.end_order_index is None:
                continue
            if not (unit.start_order_index <= hit.order_index <= unit.end_order_index):
                continue
            document = self._load_document_by_version(unit.document_version_id)
            if document is None:
                continue
            contextual_hits.append(
                RetrievalHit(
                    source_kind="retrieval_unit_context",
                    score=min(1.1, hit.score + 0.12),
                    document_id=document.id,
                    document_version_id=unit.document_version_id,
                    document_display_code=document.display_code,
                    document_title=document.title,
                    node_id=unit.anchor_node_id,
                    retrieval_unit_id=unit.id,
                    order_index=unit.start_order_index,
                    locator=unit.locator_primary,
                    heading_path=unit.heading_path,
                    text=unit.text,
                )
            )
        return contextual_hits


def _document_fallback_text(document: Document) -> str:
    return " ".join(part for part in (document.display_code, document.normalized_code, document.title) if part)


def _topic_document_priors(query: ParsedQuery) -> set[str]:
    tokens = query.lexical_tokens
    priors: set[str] = set()
    for triggers, families in _TOPIC_DOCUMENT_PRIORS:
        if any(any(token.startswith(trigger) for trigger in triggers) for token in tokens):
            priors.update(families)
    return priors


def _document_matches_family(display_code: str, family: str) -> bool:
    return _document_family(display_code) == _document_family(family)


def _document_family(display_code: str | None) -> str:
    normalized = normalize_document_code(display_code or "")
    family_match = re.match(r"^([A-ZА-ЯЁ]+)\s*(\d+)", normalized)
    if family_match:
        return f"{family_match.group(1)}{family_match.group(2)}"
    return re.sub(r"([.\-/])\d{4}$", "", normalized)


def _shares_document_family(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return _document_family(left) == _document_family(right)


def _is_legacy_document(display_code: str | None, title: str | None) -> bool:
    normalized_code = normalize_document_code(display_code or "")
    normalized_title = (title or "").casefold()
    return normalized_code.startswith(("СНИП", "SNIP")) or "пособ" in normalized_title


def _extract_year(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR_RE.search(value)
    return int(match.group(0)) if match else None


def _match_locator_value(query_locator: str, candidate_locator: str | None, heading_path: str | None) -> float:
    normalized_query = (query_locator or "").strip()
    normalized_locator = (candidate_locator or "").strip()
    heading = (heading_path or "").casefold()
    if normalized_locator == normalized_query and normalized_locator:
        return 1.06
    if normalized_locator and normalized_locator.startswith(f"{normalized_query}."):
        return 0.96
    if normalized_query and normalized_query.startswith(f"{normalized_locator}.") and normalized_locator:
        return 0.9
    if normalized_query and normalized_query.casefold() in heading:
        return 0.82
    return 0.0


def _dedupe_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    seen: set[tuple[str, UUID | None, UUID | None]] = set()
    ordered: list[RetrievalHit] = []
    for hit in sorted(hits, key=lambda item: (-item.score, item.order_index or 0)):
        key = (hit.source_kind, hit.node_id, hit.retrieval_unit_id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(hit)
    return ordered
