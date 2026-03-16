"""Deterministic parsing of user retrieval queries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from qanorm.indexing.fts import tokenize_for_fts
from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.locators import normalize_locator_value
from qanorm.utils.text import normalize_whitespace


_DOCUMENT_CODE_RE = re.compile(
    r"\b(?:СП|СНиП|СНИП|ГОСТ|SP|SNIP|GOST)\s*[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9.\-/]*",
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
_COMPACT_PREFIX_RE = re.compile(
    r"\b(СП|СНиП|СНИП|ГОСТ|SP|SNIP|GOST)(\d)",
    re.IGNORECASE,
)
_YEAR_SUFFIX_RE = re.compile(r"[-./](\d{4})$")


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

        normalized_text = _expand_compact_document_prefixes(normalize_whitespace(text))
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
            variant
            for match in _DOCUMENT_CODE_RE.finditer(normalized_text)
            for variant in _expand_document_code_variants(normalize_document_code(match.group(0)))
        )
        locator_values = _dedupe_preserve_order(
            normalized
            for normalized in (
                normalize_locator_value(match.group(0))
                for match in (
                    list(_NUMERIC_LOCATOR_RE.finditer(normalized_text))
                    + list(_APPENDIX_LOCATOR_RE.finditer(normalized_text))
                )
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


def _expand_compact_document_prefixes(text: str) -> str:
    """Insert a missing space between a known document prefix and its numeric code."""

    return _COMPACT_PREFIX_RE.sub(r"\1 \2", text)


def _expand_document_code_variants(code: str) -> list[str]:
    """Expand one detected code into compact and family variants used by retrieval."""

    cleaned = normalize_document_code(code)
    if not cleaned or " " not in cleaned:
        return [cleaned] if cleaned else []

    prefix, rest = cleaned.split(" ", 1)
    variants = [cleaned, f"{prefix}{rest}"]

    yearless = _strip_trailing_year(rest)
    if yearless and yearless != rest:
        variants.extend([f"{prefix} {yearless}", f"{prefix}{yearless}"])

    first_segment = rest.split(".", 1)[0]
    if first_segment:
        variants.extend([f"{prefix} {first_segment}", f"{prefix}{first_segment}"])
    return _dedupe_preserve_order(value for value in variants if value)


def _strip_trailing_year(value: str) -> str:
    """Drop a trailing year suffix such as `.2018` or `-2014` from one code tail."""

    return _YEAR_SUFFIX_RE.sub("", value).strip()


def _dedupe_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
