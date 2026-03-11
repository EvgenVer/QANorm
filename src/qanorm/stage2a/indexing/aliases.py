"""Deterministic builders for canonical document aliases."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from urllib.parse import urlparse

from qanorm.models import Document, DocumentAlias, DocumentSource
from qanorm.normalizers.codes import clean_document_code
from qanorm.utils.text import normalize_whitespace


_ALIAS_PUNCT_RE = re.compile(r"[\s,;:()\\[\\]\"'`]+")
_PREFIX_VARIANTS = {
    "SP": ("SP", "СП"),
    "СП": ("СП", "SP"),
    "SNIP": ("SNIP", "СНИП", "СНиП"),
    "СНИП": ("СНИП", "SNIP", "СНиП"),
    "СНиП": ("СНиП", "СНИП", "SNIP"),
    "GOST": ("GOST", "ГОСТ"),
    "ГОСТ": ("ГОСТ", "GOST"),
}


@dataclass(frozen=True, slots=True)
class DocumentAliasDraft:
    """One derived alias candidate before ORM persistence."""

    alias_raw: str
    alias_normalized: str
    alias_type: str
    confidence: float


def build_document_alias_drafts(
    document: Document,
    *,
    sources: Iterable[DocumentSource],
) -> list[DocumentAliasDraft]:
    """Build deterministic alias candidates for one canonical document."""

    drafts_by_key: dict[str, DocumentAliasDraft] = {}

    def add_alias(raw: str | None, alias_type: str, confidence: float) -> None:
        if raw is None:
            return
        normalized = normalize_alias_value(raw)
        if normalized is None:
            return
        if len(normalized) > 255:
            return
        candidate = DocumentAliasDraft(
            alias_raw=normalize_whitespace(raw),
            alias_normalized=normalized,
            alias_type=alias_type,
            confidence=confidence,
        )
        key = candidate.alias_normalized
        existing = drafts_by_key.get(key)
        if existing is None or candidate.confidence > existing.confidence or (
            candidate.confidence == existing.confidence and candidate.alias_type < existing.alias_type
        ):
            drafts_by_key[key] = candidate

    for code in _derive_code_aliases(document.display_code):
        add_alias(code, "display_code", 1.0)
    for code in _derive_code_aliases(document.normalized_code):
        add_alias(code, "normalized_code", 1.0)
    add_alias(document.title, "title", 0.95)
    add_alias(_compact_title(document.title), "title_compact", 0.8)

    for source in sources:
        for url, alias_type in (
            (source.card_url, "card_url"),
            (source.html_url, "html_url"),
            (source.pdf_url, "pdf_url"),
            (source.print_url, "print_url"),
        ):
            add_alias(url, alias_type, 0.6)

    return sorted(drafts_by_key.values(), key=lambda item: (item.alias_type, item.alias_normalized))


def build_document_alias_models(
    document: Document,
    *,
    sources: Iterable[DocumentSource],
) -> list[DocumentAlias]:
    """Build ORM models ready for persistence."""

    return [
        DocumentAlias(
            document_id=document.id,
            alias_raw=draft.alias_raw,
            alias_normalized=draft.alias_normalized,
            alias_type=draft.alias_type,
            confidence=draft.confidence,
        )
        for draft in build_document_alias_drafts(document, sources=sources)
    ]


def normalize_alias_value(value: str | None) -> str | None:
    """Normalize a document alias into a lookup-friendly string."""

    if value is None:
        return None

    normalized = normalize_whitespace(value)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        normalized_url = f"{parsed.netloc}{path}"
        if parsed.query:
            normalized_url = f"{normalized_url}?{parsed.query}"
        return normalized_url.casefold()

    compact = _ALIAS_PUNCT_RE.sub(" ", normalized).strip()
    compact = normalize_whitespace(compact)
    if not compact:
        return None
    return compact.casefold()


def _derive_code_aliases(code: str | None) -> list[str]:
    if code is None:
        return []

    cleaned = clean_document_code(code)
    if not cleaned:
        return []

    aliases = {cleaned}
    parts = cleaned.split(" ", 1)
    if len(parts) != 2:
        return sorted(aliases)

    prefix, rest = parts[0], parts[1]
    for prefix_variant in _PREFIX_VARIANTS.get(prefix.upper(), (prefix,)):
        aliases.add(f"{prefix_variant} {rest}")
        for shortened_rest in _derive_shortened_code_variants(rest):
            aliases.add(f"{prefix_variant} {shortened_rest}")
    return sorted(aliases)


def _derive_shortened_code_variants(rest: str) -> list[str]:
    variants = {rest}
    pieces = rest.split(".")
    if len(pieces) >= 3 and pieces[-1].isdigit() and len(pieces[-1]) == 4:
        variants.add(".".join(pieces[:-1]))
    head = pieces[0]
    if head:
        variants.add(head)
    if len(pieces) >= 2:
        variants.add(".".join(pieces[:2]))
    return sorted(variant for variant in variants if variant)


def _compact_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = normalize_whitespace(title)
    if not normalized:
        return None
    return normalized.split(". ", 1)[0]
