"""Deterministic parsing of user retrieval queries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from qanorm.indexing.fts import tokenize_for_fts
from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.locators import normalize_locator_value
from qanorm.utils.text import normalize_whitespace


_DOCUMENT_CODE_RE = re.compile(
    r"\b(?:СП|СНиП|ГОСТ|SP|SNIP|GOST)\s*[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9.\-\/]*",
    re.IGNORECASE,
)
_NUMERIC_LOCATOR_RE = re.compile(
    r"\b(?:п\.?|пункт|раздел|глава|табл\.?|таблица|section|chapter|table)\s*[0-9]+(?:\.[0-9]+)*\b",
    re.IGNORECASE,
)
_APPENDIX_LOCATOR_RE = re.compile(
    r"\b(?:прил\.?|приложение|appendix)\s*[A-Za-zА-Яа-я]\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """Deterministic extraction result for one user query."""

    raw_text: str
    normalized_text: str
    explicit_document_codes: list[str]
    explicit_locator_values: list[str]
    lexical_query: str
    lexical_tokens: list[str]


class QueryParser:
    """Extract document and locator hints without LLM involvement."""

    def parse(self, text: str) -> ParsedQuery:
        """Parse one query into deterministic retrieval hints."""

        normalized_text = normalize_whitespace(text)
        if not normalized_text:
            return ParsedQuery(
                raw_text=text,
                normalized_text="",
                explicit_document_codes=[],
                explicit_locator_values=[],
                lexical_query="",
                lexical_tokens=[],
            )

        document_codes = _dedupe_preserve_order(
            normalize_document_code(match.group(0))
            for match in _DOCUMENT_CODE_RE.finditer(normalized_text)
        )
        locator_values = _dedupe_preserve_order(
            normalized
            for normalized in (
                normalize_locator_value(match.group(0))
                for match in list(_NUMERIC_LOCATOR_RE.finditer(normalized_text)) + list(_APPENDIX_LOCATOR_RE.finditer(normalized_text))
            )
            if normalized is not None
        )
        lexical_tokens = tokenize_for_fts(normalized_text)

        return ParsedQuery(
            raw_text=text,
            normalized_text=normalized_text,
            explicit_document_codes=document_codes,
            explicit_locator_values=locator_values,
            lexical_query=" ".join(lexical_tokens),
            lexical_tokens=lexical_tokens,
        )


def _dedupe_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
