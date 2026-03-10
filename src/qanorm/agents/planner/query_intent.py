"""Intent-gate helpers for Stage 2 query understanding."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from qanorm.utils.text import normalize_whitespace


DOCUMENT_CODE_RE = re.compile(
    r"\b(?:ГОСТ|СП|SP|СНиП|РД|СТО|ВСП|ISO|EN)\s*[-–]?\s*\d+(?:\.\d+)*(?:[-/]\d+(?:\.\d+)*)*",
    re.IGNORECASE,
)
LOCATOR_RES = (
    re.compile(r"\b(?:п\.|пункт)\s*\d+(?:[./]\d+)*(?:[a-zа-я])?\b", re.IGNORECASE),
    re.compile(r"\b(?:табл\.|таблица)\s*[A-Za-zА-Яа-я0-9.-]+\b", re.IGNORECASE),
    re.compile(r"\b(?:раздел|глава)\s*\d+(?:[./]\d+)*\b", re.IGNORECASE),
    re.compile(r"\b(?:прил\.|приложение)\s*[A-Za-zА-Яа-я0-9.-]+\b", re.IGNORECASE),
    re.compile(r"\b(?:абз\.|абзац)\s*\d+\b", re.IGNORECASE),
)
QUESTION_SPLIT_RE = re.compile(r"[?;]|(?:,?\s+и\s+)", re.IGNORECASE)
NON_ENGINEERING_PATTERNS = (
    "привет",
    "здравств",
    "как дела",
    "hello",
    "hi",
    "расскажи анекдот",
    "погода",
    "курс валют",
    "кто ты",
)
GENERALITY_PATTERNS = (
    "что по нормам",
    "какие нормы",
    "что говорят нормы",
    "что требуется",
    "что допускается",
)
ENGINEERING_HINTS = (
    "бетон",
    "арматур",
    "пожар",
    "эвакуац",
    "нагруз",
    "перекрыт",
    "фундамент",
    "колонн",
    "стен",
    "фасад",
    "здани",
    "сооруж",
    "лестниц",
    "fire",
    "safety",
    "evacuation",
    "bridge",
    "culvert",
    "station",
    "requirement",
    "requirements",
    "проект",
    "монтаж",
    "строит",
    "конструкц",
    "огнестойк",
)
NORMATIVE_HINTS = (
    "норм",
    "требован",
    "допускается",
    "обязательно",
    "по сп",
    "по гост",
    "по снип",
    "пункт",
    "clause",
    "section",
    "table",
    "requirement",
    "required",
    "раздел",
    "таблица",
    "приложение",
)
TRUSTED_WEB_HINTS = ("разъяснен", "комментар", "практик", "рекомендац", "письмо", "обзор")
OPEN_WEB_HINTS = ("в интернете", "в сети", "open web", "web", "найди")
CONSTRAINT_MARKERS = ("при ", "если ", "для ", "без ", "с учетом ", "в условиях ", "при наличии ")


class QueryIntent(StrEnum):
    """Top-level gate result that decides whether retrieval should run."""

    CLARIFY = "clarify"
    NO_RETRIEVAL = "no_retrieval"
    NORMATIVE_RETRIEVAL = "normative_retrieval"
    MIXED_RETRIEVAL = "mixed_retrieval"


class RetrievalMode(StrEnum):
    """Persisted retrieval mode derived from the intent gate."""

    CLARIFY = "clarify"
    NONE = "none"
    NORMATIVE = "normative"
    MIXED = "mixed"


@dataclass(slots=True, frozen=True)
class QueryIntentResult:
    """Structured understanding extracted from the raw user request."""

    intent: QueryIntent
    retrieval_mode: RetrievalMode
    clarification_required: bool
    clarification_question: str | None = None
    document_hints: list[str] = field(default_factory=list)
    locator_hints: list[str] = field(default_factory=list)
    subject: str | None = None
    engineering_aspects: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    requires_trusted_web: bool = False
    requires_open_web: bool = False

    @property
    def requires_normative_retrieval(self) -> bool:
        """Return whether the request should reach normative retrieval."""

        return self.intent in {QueryIntent.NORMATIVE_RETRIEVAL, QueryIntent.MIXED_RETRIEVAL}


def infer_query_intent(query_text: str) -> QueryIntentResult:
    """Infer intent and hints conservatively before any expensive retrieval runs."""

    normalized_text = normalize_whitespace(query_text)
    lowered = normalized_text.lower()
    document_hints = extract_document_hints(normalized_text)
    locator_hints = extract_locator_hints(normalized_text)
    aspects = extract_engineering_aspects(normalized_text)
    constraints = extract_constraints(normalized_text)
    subject = extract_subject(
        normalized_text,
        document_hints=document_hints,
        locator_hints=locator_hints,
    )

    has_normative_signal = bool(document_hints or locator_hints) or any(token in lowered for token in NORMATIVE_HINTS)
    has_engineering_signal = has_normative_signal or any(token in lowered for token in ENGINEERING_HINTS)
    has_trusted_signal = any(token in lowered for token in TRUSTED_WEB_HINTS)
    has_open_signal = any(token in lowered for token in OPEN_WEB_HINTS)
    is_non_engineering = any(token in lowered for token in NON_ENGINEERING_PATTERNS)
    only_document_reference = bool(document_hints) and len(_strip_known_hints(normalized_text, document_hints, locator_hints)) < 18
    locator_without_document = bool(locator_hints) and not document_hints
    too_short = len(normalized_text) < 12
    too_general = any(token in lowered for token in GENERALITY_PATTERNS) and not document_hints and not locator_hints
    low_information = len(subject or "") < 14 and not document_hints

    if is_non_engineering or _looks_like_garbage(normalized_text):
        return QueryIntentResult(
            intent=QueryIntent.NO_RETRIEVAL,
            retrieval_mode=RetrievalMode.NONE,
            clarification_required=False,
            document_hints=document_hints,
            locator_hints=locator_hints,
            subject=subject,
            engineering_aspects=aspects,
            constraints=constraints,
        )

    if only_document_reference or locator_without_document or too_short or too_general or (has_normative_signal and low_information):
        return QueryIntentResult(
            intent=QueryIntent.CLARIFY,
            retrieval_mode=RetrievalMode.CLARIFY,
            clarification_required=True,
            clarification_question=build_clarification_question(
                query_text=normalized_text,
                document_hints=document_hints,
                locator_hints=locator_hints,
                subject=subject,
            ),
            document_hints=document_hints,
            locator_hints=locator_hints,
            subject=subject,
            engineering_aspects=aspects,
            constraints=constraints,
            requires_trusted_web=has_trusted_signal,
            requires_open_web=has_open_signal,
        )

    if has_engineering_signal and (has_trusted_signal or has_open_signal):
        return QueryIntentResult(
            intent=QueryIntent.MIXED_RETRIEVAL,
            retrieval_mode=RetrievalMode.MIXED,
            clarification_required=False,
            document_hints=document_hints,
            locator_hints=locator_hints,
            subject=subject,
            engineering_aspects=aspects,
            constraints=constraints,
            requires_trusted_web=has_trusted_signal,
            requires_open_web=has_open_signal,
        )

    if has_engineering_signal:
        return QueryIntentResult(
            intent=QueryIntent.NORMATIVE_RETRIEVAL,
            retrieval_mode=RetrievalMode.NORMATIVE,
            clarification_required=False,
            document_hints=document_hints,
            locator_hints=locator_hints,
            subject=subject,
            engineering_aspects=aspects,
            constraints=constraints,
        )

    return QueryIntentResult(
        intent=QueryIntent.NO_RETRIEVAL,
        retrieval_mode=RetrievalMode.NONE,
        clarification_required=False,
        document_hints=document_hints,
        locator_hints=locator_hints,
        subject=subject,
        engineering_aspects=aspects,
        constraints=constraints,
        requires_trusted_web=has_trusted_signal,
        requires_open_web=has_open_signal,
    )


def extract_document_hints(query_text: str) -> list[str]:
    """Extract short document-code hints like `СП 63` or `ГОСТ 21.501`."""

    hints: list[str] = []
    for match in DOCUMENT_CODE_RE.finditer(query_text):
        value = _normalize_hint(match.group(0).upper().replace("СНИП", "СНиП"))
        if value not in hints:
            hints.append(value)
    return hints


def extract_locator_hints(query_text: str) -> list[str]:
    """Extract locator fragments that can scope retrieval later."""

    hints: list[str] = []
    for pattern in LOCATOR_RES:
        for match in pattern.finditer(query_text):
            value = _normalize_hint(match.group(0))
            if value not in hints:
                hints.append(value)
    return hints


def extract_engineering_aspects(query_text: str) -> list[str]:
    """Split the request into coarse engineering aspects."""

    parts = [normalize_whitespace(part.strip(" .,\n\t")) for part in QUESTION_SPLIT_RE.split(query_text)]
    return [part for part in parts if len(part) >= 12][:6] or [normalize_whitespace(query_text)]


def extract_constraints(query_text: str) -> list[str]:
    """Extract short constraint clauses that should survive planning."""

    lowered = query_text.lower()
    constraints: list[str] = []
    for marker in CONSTRAINT_MARKERS:
        index = lowered.find(marker)
        if index < 0:
            continue
        snippet = normalize_whitespace(query_text[index : index + 120].strip(" .,"))
        if snippet and snippet not in constraints:
            constraints.append(snippet)
    return constraints[:4]


def extract_subject(
    query_text: str,
    *,
    document_hints: list[str] | None = None,
    locator_hints: list[str] | None = None,
) -> str | None:
    """Strip obvious document references and keep the remaining subject text."""

    stripped = _strip_known_hints(query_text, document_hints or [], locator_hints or [])
    stripped = re.sub(
        r"\b(?:по|согласно|в|на|для|как|какие|какой|нужно|требуется|что)\b",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    subject = normalize_whitespace(stripped.strip(" .,:;"))
    return subject[:180] or None


def build_clarification_question(
    *,
    query_text: str,
    document_hints: list[str],
    locator_hints: list[str],
    subject: str | None,
) -> str:
    """Generate a deterministic clarification question instead of noisy retrieval."""

    if len(document_hints) > 1:
        joined = ", ".join(document_hints[:3])
        return f"Уточните, по какому документу нужен ответ: {joined}?"
    if locator_hints and not document_hints:
        return f"Уточните, к какому документу относится локатор `{locator_hints[0]}`."
    if document_hints and not subject:
        return (
            f"Уточните, что именно нужно проверить по `{document_hints[0]}`: "
            "конкретный пункт, таблицу, требование или инженерный сценарий."
        )
    if not subject:
        return "Уточните объект, аспект проверки и условия применения нормы."
    return (
        "Уточните запрос точнее: какой документ, пункт или инженерный аспект нужно проверить "
        f"по теме `{subject}`?"
    )


def _normalize_hint(value: str) -> str:
    """Normalize spacing inside extracted hints without changing semantics."""

    normalized = normalize_whitespace(value)
    normalized = normalized.replace("СП63", "СП 63")
    normalized = normalized.replace("ГОСТ21", "ГОСТ 21")
    return normalized


def _strip_known_hints(query_text: str, document_hints: list[str], locator_hints: list[str]) -> str:
    """Remove document and locator references from the surface subject."""

    stripped = query_text
    for hint in [*document_hints, *locator_hints]:
        stripped = re.sub(re.escape(hint), " ", stripped, flags=re.IGNORECASE)
    return normalize_whitespace(stripped)


def _looks_like_garbage(query_text: str) -> bool:
    """Detect empty and obviously non-actionable inputs before retrieval starts."""

    if not query_text.strip():
        return True
    alnum_count = sum(char.isalnum() for char in query_text)
    if alnum_count < max(3, len(query_text) // 5):
        return True
    unique_tokens = {token for token in re.split(r"\s+", query_text.lower()) if token}
    return len(unique_tokens) <= 1 and len(query_text) <= 16
