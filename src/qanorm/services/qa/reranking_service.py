"""Reranking helpers for primary and secondary evidence selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from qanorm.providers.base import ProviderCapabilities, ProviderName, RerankRequest, RerankResponse, RerankResult, RerankerProvider
from qanorm.services.qa.query_rewriter import QueryRewrite
from qanorm.utils.text import normalize_whitespace


TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{2,}", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(normalize_whitespace(text))}


@dataclass(slots=True, frozen=True)
class RankedEvidenceCandidate:
    """One reranked evidence candidate with selection rationale."""

    chunk_id: Any
    score: float
    tier: str
    rationale: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RerankingSelection:
    """Selected primary and secondary hits returned to retrieval."""

    primary_hits: list[Any]
    secondary_hits: list[Any]
    dropped_hits: list[Any]


class CodeFirstRerankerProvider(RerankerProvider):
    """Deterministic fallback reranker used when no model-based reranker is configured."""

    provider_name: ProviderName = "ollama"
    capabilities = ProviderCapabilities(rerank=True)

    def __init__(self) -> None:
        self.model = "code-first-fallback"

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        query_tokens = _tokenize(request.query)
        results: list[RerankResult] = []
        for index, document in enumerate(request.documents):
            overlap = len(query_tokens & _tokenize(document)) / max(1, len(query_tokens))
            results.append(RerankResult(index=index, document=document, score=round(overlap, 6)))
        results.sort(key=lambda item: (-item.score, item.index))
        if request.top_k is not None:
            results = results[: request.top_k]
        return RerankResponse(provider=self.provider_name, model=self.model, results=results)


class RerankingService:
    """Apply provider reranking or deterministic fallback to retrieval candidates."""

    def __init__(self, *, provider: RerankerProvider | None = None) -> None:
        self.provider = provider or CodeFirstRerankerProvider()

    async def select_hits(
        self,
        *,
        query_rewrite: QueryRewrite,
        hits: list[Any],
        primary_limit: int,
        secondary_limit: int,
        primary_threshold: float = 0.48,
        secondary_threshold: float = 0.4,
    ) -> RerankingSelection:
        """Split candidates into primary, secondary, and dropped buckets."""

        if not hits:
            return RerankingSelection(primary_hits=[], secondary_hits=[], dropped_hits=[])

        document_texts = [self._render_document(hit) for hit in hits]
        response = await self.provider.rerank(
            RerankRequest(
                model=getattr(self.provider, "model", "rerank"),
                query=query_rewrite.semantic_query,
                documents=document_texts,
                top_k=len(document_texts),
                metadata=query_rewrite.to_payload(),
            )
        )
        provider_scores = {item.index: item.score for item in response.results}

        ranked_rows: list[tuple[float, Any]] = []
        for index, hit in enumerate(hits):
            fallback_score, rationale = self._fallback_score(hit, query_rewrite=query_rewrite)
            provider_score = provider_scores.get(index, fallback_score)
            final_score = round((fallback_score * 0.6) + (provider_score * 0.4), 6)
            selection_tier = "dropped"
            if final_score >= primary_threshold:
                selection_tier = "primary"
            elif final_score >= secondary_threshold:
                selection_tier = "secondary"
            updated_hit = replace(
                hit,
                score=final_score,
                selection_tier=selection_tier,
                retrieval_metadata={
                    **getattr(hit, "retrieval_metadata", {}),
                    "rerank_model": getattr(self.provider, "model", "rerank"),
                    "rerank_provider": getattr(self.provider, "provider_name", "unknown"),
                    "fallback_score": round(fallback_score, 6),
                    "provider_score": round(provider_score, 6),
                    "ranking_rationale": rationale,
                },
            )
            ranked_rows.append((final_score, updated_hit))

        ranked_rows.sort(
            key=lambda item: (
                -item[0],
                getattr(item[1], "selection_tier", "dropped") != "primary",
                "exact" not in getattr(item[1], "score_source", ""),
                getattr(item[1], "locator", "") or "",
            )
        )

        primary_hits = [hit for _, hit in ranked_rows if hit.selection_tier == "primary"][:primary_limit]
        secondary_hits = [hit for _, hit in ranked_rows if hit.selection_tier == "secondary"][:secondary_limit]
        dropped_hits = [hit for _, hit in ranked_rows if hit.selection_tier == "dropped"]
        return RerankingSelection(primary_hits=primary_hits, secondary_hits=secondary_hits, dropped_hits=dropped_hits)

    def _render_document(self, hit: Any) -> str:
        """Build one reranker document payload from the retrieval candidate."""

        return normalize_whitespace(
            " ".join(
                [
                    getattr(hit, "document_code", "") or "",
                    getattr(hit, "document_title", "") or "",
                    getattr(hit, "locator", "") or "",
                    getattr(hit, "quote", "") or "",
                    getattr(hit, "chunk_text", "") or "",
                ]
            )
        )

    def _fallback_score(self, hit: Any, *, query_rewrite: QueryRewrite) -> tuple[float, list[str]]:
        """Score candidates deterministically so retrieval still works without model rerank."""

        rationale: list[str] = []
        query_tokens = _tokenize(query_rewrite.lexical_query or query_rewrite.semantic_query)
        hit_tokens = _tokenize(self._render_document(hit))
        overlap = len(query_tokens & hit_tokens) / max(1, len(query_tokens))
        score = overlap * 0.55
        if "exact" in getattr(hit, "score_source", ""):
            score += 0.22
            rationale.append("exact_match")
        if "fts" in getattr(hit, "score_source", ""):
            score += 0.12
            rationale.append("lexical_match")
        if "vector" in getattr(hit, "score_source", ""):
            score += 0.08
            rationale.append("semantic_match")
        if query_rewrite.document_hint and query_rewrite.document_hint.casefold() in self._render_document(hit).casefold():
            score += 0.12
            rationale.append("document_hint_match")
        if query_rewrite.locator_hint:
            locator_haystack = " ".join([getattr(hit, "locator", "") or "", getattr(hit, "locator_end", "") or ""]).casefold()
            if query_rewrite.locator_hint.casefold() in locator_haystack:
                score += 0.22
                rationale.append("locator_match")
        if overlap >= 0.45:
            rationale.append("token_overlap")
        return min(score, 1.0), rationale or ["low_signal_match"]
