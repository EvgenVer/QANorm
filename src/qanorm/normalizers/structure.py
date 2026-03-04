"""Document structure normalization."""

from __future__ import annotations

from dataclasses import dataclass
import re

from qanorm.normalizers.codes import normalize_document_code
from qanorm.normalizers.locators import build_node_locator
from qanorm.utils.text import normalize_whitespace


_PAGE_MARKER_RE = re.compile(
    r"^(?:\[\[\s*PAGE[:\s]+(?P<bracket>\d+)\s*\]\]|\[\s*PAGE\s+(?P<plain>\d+)\s*\]|---\s*PAGE\s+(?P<dash>\d+)\s*---)$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^(?:РАЗДЕЛ|SECTION|ГЛАВА|CHAPTER)\s+(?P<label>[IVXLC0-9]+)\b[.\-:\s]*(?P<title>.+)?$")
_SUBSECTION_RE = re.compile(r"^(?:ПОДРАЗДЕЛ|SUBSECTION)\s+(?P<label>\d+(?:\.\d+)*)\b[.\-:\s]*(?P<title>.+)?$")
_NUMBERED_SUBSECTION_RE = re.compile(r"^(?P<label>\d+\.\d+)\s+(?P<title>.+)$")
_POINT_RE = re.compile(r"^(?P<label>\d+)\.\s+(?P<title>.+)$")
_SUBPOINT_RE = re.compile(r"^(?:(?P<num>\d+(?:\.\d+){2,})\s+(?P<num_text>.+)|(?P<letter>[A-Za-zА-Яа-яЁё])\)\s+(?P<letter_text>.+)|-\s+(?P<dash_text>.+))$")
_APPENDIX_RE = re.compile(r"^(?:ПРИЛОЖЕНИЕ|APPENDIX)\s+(?P<label>[A-Za-zА-Яа-яЁё0-9]+)?[.\-:\s]*(?P<title>.+)?$", re.IGNORECASE)
_TABLE_RE = re.compile(r"^(?:ТАБЛИЦА|TABLE)\s+(?P<label>[A-Za-zА-Яа-яЁё0-9.\-]+)?[.\-:\s]*(?P<title>.+)?$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^(?:ПРИМЕЧАНИЕ|NOTE)\b[.\-:\s]*(?P<title>.+)?$", re.IGNORECASE)
_REFERENCE_RE = re.compile(
    r"\b(?P<raw>(?:СП|SP|ГОСТ|GOST|СНИП|СНиП|SNIP)\s*[A-Za-zА-Яа-яЁё]?\s*\d[\d./-]*)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class PreparedLine:
    """One normalized content line prepared for structural parsing."""

    text: str
    char_start: int
    char_end: int
    page_number: int | None


@dataclass(slots=True)
class PreparedStructureText:
    """Normalized text and metadata used for structural parsing."""

    text: str
    lines: list[PreparedLine]


@dataclass(slots=True)
class StructuralNodeDraft:
    """In-memory node produced by the structure parser."""

    node_type: str
    text: str
    label: str | None
    title: str | None
    order_index: int
    parent_order_index: int | None
    locator: str
    page_from: int | None
    page_to: int | None
    char_start: int | None
    char_end: int | None
    parse_confidence: float | None


@dataclass(slots=True)
class DocumentReferenceDraft:
    """In-memory reference extracted from normalized nodes."""

    source_order_index: int
    reference_text: str
    referenced_code_normalized: str
    match_confidence: float


@dataclass(slots=True)
class StructureNormalizationResult:
    """Result of structural parsing before persistence."""

    prepared_text: PreparedStructureText
    nodes: list[StructuralNodeDraft]
    references: list[DocumentReferenceDraft]


def prepare_text_for_structure_parsing(text: str) -> PreparedStructureText:
    """Normalize raw extracted text into content lines for structural parsing."""

    normalized_input = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace("\t", " ")
    current_page: int | None = None
    prepared_lines: list[PreparedLine] = []
    content_parts: list[str] = []
    cursor = 0

    for raw_line in normalized_input.split("\n"):
        normalized_line = normalize_whitespace(raw_line)
        if not normalized_line:
            continue

        page_match = _PAGE_MARKER_RE.match(normalized_line)
        if page_match:
            page_value = page_match.group("bracket") or page_match.group("plain") or page_match.group("dash")
            current_page = int(page_value)
            continue

        char_start = cursor
        char_end = char_start + len(normalized_line)
        prepared_lines.append(
            PreparedLine(
                text=normalized_line,
                char_start=char_start,
                char_end=char_end,
                page_number=current_page,
            )
        )
        content_parts.append(normalized_line)
        cursor = char_end + 1

    return PreparedStructureText(
        text="\n".join(content_parts),
        lines=prepared_lines,
    )


def normalize_document_structure_text(
    text: str,
    *,
    parse_confidence: float | None = None,
) -> StructureNormalizationResult:
    """Parse a document text into structural nodes and document references."""

    prepared = prepare_text_for_structure_parsing(text)
    if not prepared.lines:
        return StructureNormalizationResult(prepared_text=prepared, nodes=[], references=[])

    nodes: list[StructuralNodeDraft] = []
    title_line = prepared.lines[0]
    title_node = _build_node(
        node_type="title",
        line=title_line,
        label=None,
        title=title_line.text,
        order_index=1,
        parent_order_index=None,
        parent_locator=None,
        parse_confidence=parse_confidence,
    )
    nodes.append(title_node)

    current_section = title_node.order_index
    current_subsection: int | None = None
    current_point: int | None = None
    current_subpoint: int | None = None

    for line in prepared.lines[1:]:
        node_type, label, title = _classify_line(line.text)
        if node_type == "section":
            parent_order_index = title_node.order_index
            parent_locator = title_node.locator
            current_section = 0
            current_subsection = None
            current_point = None
            current_subpoint = None
        elif node_type == "subsection":
            parent_order_index = current_section or title_node.order_index
            parent_locator = nodes[parent_order_index - 1].locator if parent_order_index else title_node.locator
            current_subsection = 0
            current_point = None
            current_subpoint = None
        elif node_type == "point":
            parent_order_index = current_subsection or current_section or title_node.order_index
            parent_locator = nodes[parent_order_index - 1].locator
            current_point = 0
            current_subpoint = None
        elif node_type == "subpoint":
            parent_order_index = current_point or current_subsection or current_section or title_node.order_index
            parent_locator = nodes[parent_order_index - 1].locator
            current_subpoint = 0
        elif node_type in {"appendix", "table", "note"}:
            parent_order_index = title_node.order_index
            parent_locator = title_node.locator
        else:
            parent_order_index = current_subpoint or current_point or current_subsection or current_section or title_node.order_index
            parent_locator = nodes[parent_order_index - 1].locator

        node = _build_node(
            node_type=node_type,
            line=line,
            label=label,
            title=title,
            order_index=len(nodes) + 1,
            parent_order_index=parent_order_index,
            parent_locator=parent_locator,
            parse_confidence=parse_confidence,
        )
        nodes.append(node)

        if node_type == "section":
            current_section = node.order_index
        elif node_type == "subsection":
            current_subsection = node.order_index
        elif node_type == "point":
            current_point = node.order_index
        elif node_type == "subpoint":
            current_subpoint = node.order_index

    references = extract_document_references(nodes)
    return StructureNormalizationResult(
        prepared_text=prepared,
        nodes=nodes,
        references=references,
    )


def extract_document_references(nodes: list[StructuralNodeDraft]) -> list[DocumentReferenceDraft]:
    """Extract and normalize inter-document references from normalized nodes."""

    extracted: list[DocumentReferenceDraft] = []
    seen: set[tuple[int, str]] = set()

    for node in nodes:
        haystack = " ".join(part for part in (node.title, node.text) if part)
        for match in _REFERENCE_RE.finditer(haystack):
            raw_reference = normalize_whitespace(match.group("raw")).rstrip(".,;:")
            normalized_reference = normalize_document_code(raw_reference)
            key = (node.order_index, normalized_reference)
            if key in seen:
                continue
            seen.add(key)
            extracted.append(
                DocumentReferenceDraft(
                    source_order_index=node.order_index,
                    reference_text=raw_reference,
                    referenced_code_normalized=normalized_reference,
                    match_confidence=1.0,
                )
            )

    return extracted


def _build_node(
    *,
    node_type: str,
    line: PreparedLine,
    label: str | None,
    title: str | None,
    order_index: int,
    parent_order_index: int | None,
    parent_locator: str | None,
    parse_confidence: float | None,
) -> StructuralNodeDraft:
    resolved_title = normalize_whitespace(title) if title else None
    resolved_label = normalize_whitespace(label) if label else None
    return StructuralNodeDraft(
        node_type=node_type,
        text=line.text,
        label=resolved_label,
        title=resolved_title,
        order_index=order_index,
        parent_order_index=parent_order_index,
        locator=build_node_locator(
            node_type=node_type,
            label=resolved_label,
            order_index=order_index,
            parent_locator=parent_locator,
        ),
        page_from=line.page_number,
        page_to=line.page_number,
        char_start=line.char_start,
        char_end=line.char_end,
        parse_confidence=parse_confidence,
    )


def _classify_line(text: str) -> tuple[str, str | None, str | None]:
    section_match = _SECTION_RE.match(text)
    if section_match:
        return "section", section_match.group("label"), section_match.group("title") or text

    subsection_match = _SUBSECTION_RE.match(text)
    if subsection_match:
        return "subsection", subsection_match.group("label"), subsection_match.group("title") or text

    appendix_match = _APPENDIX_RE.match(text)
    if appendix_match:
        return "appendix", appendix_match.group("label"), appendix_match.group("title") or text

    table_match = _TABLE_RE.match(text)
    if table_match:
        return "table", table_match.group("label"), table_match.group("title") or text
    if "|" in text:
        return "table", None, text

    note_match = _NOTE_RE.match(text)
    if note_match:
        return "note", None, note_match.group("title") or text

    numbered_subsection_match = _NUMBERED_SUBSECTION_RE.match(text)
    if numbered_subsection_match:
        return "subsection", numbered_subsection_match.group("label"), numbered_subsection_match.group("title")

    subpoint_match = _SUBPOINT_RE.match(text)
    if subpoint_match:
        label = subpoint_match.group("num") or subpoint_match.group("letter")
        title = subpoint_match.group("num_text") or subpoint_match.group("letter_text") or subpoint_match.group("dash_text")
        return "subpoint", label, title

    point_match = _POINT_RE.match(text)
    if point_match:
        return "point", point_match.group("label"), point_match.group("title")

    return "paragraph", None, None
