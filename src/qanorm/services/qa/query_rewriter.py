"""Heuristic retrieval-query rewriting for normative Stage 2 searches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from qanorm.normalizers.codes import clean_document_code
from qanorm.utils.text import normalize_whitespace


DOCUMENT_HINT_RE = re.compile(r"\b(?:СП|ГОСТ|СНиП|РД|ТСН)\s*\d[\d.\-]*", re.IGNORECASE)
LOCATOR_HINT_RE = re.compile(
    r"\b(?:пункт|п\.|таблица|табл\.|раздел|разд\.|приложение|прил\.)\s*[0-9]+(?:[./][0-9]+)*[а-яa-z]?\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё.\-]{2,}", re.UNICODE)
STOPWORDS = {
    "как",
    "какие",
    "какой",
    "какая",
    "какое",
    "можно",
    "нужно",
    "ли",
    "для",
    "про",
    "или",
    "это",
    "что",
    "по",
    "из",
    "в",
    "на",
}


@dataclass(slots=True, frozen=True)
class QueryRewrite:
    """Parallel retrieval-query views built from one user question."""

    exact_query: str
    lexical_query: str
    semantic_query: str
    document_hint: str | None = None
    locator_hint: str | None = None
    keywords: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        """Expose the rewrite for audit and evidence metadata."""

        return {
            "exact_query": self.exact_query,
            "lexical_query": self.lexical_query,
            "semantic_query": self.semantic_query,
            "document_hint": self.document_hint,
            "locator_hint": self.locator_hint,
            "keywords": list(self.keywords),
        }


class QueryRewriter:
    """Build exact, lexical, and semantic retrieval queries from one prompt."""

    def rewrite(
        self,
        query_text: str,
        *,
        document_hints: list[str] | None = None,
        locator_hints: list[str] | None = None,
        subject: str | None = None,
        engineering_aspects: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> QueryRewrite:
        """Derive three query variants tuned for different retrieval channels."""

        normalized_query = normalize_whitespace(query_text)
        document_hint = self._pick_document_hint(normalized_query, document_hints or [])
        locator_hint = self._pick_locator_hint(normalized_query, locator_hints or [])
        keywords = self._build_keywords(
            normalized_query,
            document_hint=document_hint,
            locator_hint=locator_hint,
            subject=subject,
            engineering_aspects=engineering_aspects or [],
            constraints=constraints or [],
        )

        exact_parts = [document_hint, locator_hint]
        if not any(exact_parts):
            exact_parts.append(normalized_query)
        exact_query = normalize_whitespace(" ".join(part for part in exact_parts if part))

        lexical_parts = [document_hint, locator_hint, " ".join(keywords[:12])]
        lexical_query = normalize_whitespace(" ".join(part for part in lexical_parts if part)) or normalized_query

        semantic_parts = [
            normalized_query,
            subject or "",
            " ".join(engineering_aspects or []),
            " ".join(constraints or []),
        ]
        semantic_query = normalize_whitespace(" ".join(part for part in semantic_parts if part))

        return QueryRewrite(
            exact_query=exact_query,
            lexical_query=lexical_query,
            semantic_query=semantic_query,
            document_hint=document_hint,
            locator_hint=locator_hint,
            keywords=keywords,
        )

    def _pick_document_hint(self, query_text: str, hints: list[str]) -> str | None:
        """Prefer analyzer hints but recover a document code directly from text when needed."""

        for hint in hints:
            cleaned = normalize_whitespace(clean_document_code(hint))
            if cleaned:
                return cleaned
        match = DOCUMENT_HINT_RE.search(query_text)
        if match is None:
            return None
        return normalize_whitespace(clean_document_code(match.group(0)))

    def _pick_locator_hint(self, query_text: str, hints: list[str]) -> str | None:
        """Prefer analyzer hints but recover a locator directly from text when needed."""

        for hint in hints:
            cleaned = normalize_whitespace(hint)
            if cleaned:
                return cleaned
        match = LOCATOR_HINT_RE.search(query_text)
        return normalize_whitespace(match.group(0)) if match is not None else None

    def _build_keywords(
        self,
        query_text: str,
        *,
        document_hint: str | None,
        locator_hint: str | None,
        subject: str | None,
        engineering_aspects: list[str],
        constraints: list[str],
    ) -> list[str]:
        """Keep the lexical query dense with technical terms and explicit hints."""

        seeded_parts = [query_text, subject or "", *engineering_aspects, *constraints]
        tokens: list[str] = []
        seen: set[str] = set()
        for token in TOKEN_RE.findall(" ".join(part for part in seeded_parts if part)):
            normalized_token = normalize_whitespace(token)
            folded = normalized_token.casefold()
            if len(normalized_token) < 2 or folded in STOPWORDS:
                continue
            if folded in seen:
                continue
            seen.add(folded)
            tokens.append(normalized_token)

        # Preserve the explicit document and locator hints at the front because they
        # are the highest-signal tokens for exact and lexical retrieval.
        for hinted in reversed([locator_hint, document_hint]):
            if hinted and hinted.casefold() not in seen:
                tokens.insert(0, hinted)
                seen.add(hinted.casefold())
        return tokens
