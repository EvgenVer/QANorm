"""Targeted corpus repair helpers for missing canonical documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from qanorm.db.session import session_scope
from qanorm.indexing.indexer import index_document_version
from qanorm.logging import get_ingestion_logger
from qanorm.normalizers.codes import normalize_document_code
from qanorm.parsers.card_parser import fetch_document_card, parse_document_card
from qanorm.repositories import DocumentRepository
from qanorm.services.document_pipeline import (
    download_document_artifacts,
    extract_document_text,
    normalize_document_structure,
    persist_document_card,
    run_document_ocr,
)
from qanorm.stage2a.indexing.backfill import (
    backfill_retrieval_unit_embeddings,
    rebuild_derived_retrieval_data,
)


logger = get_ingestion_logger()

_PLACEHOLDER_CODES = ("SP 1.0",)
_TARGET_CARD_URLS: tuple[str, ...] = (
    "https://meganorm.ru/Index/58/58469.htm",
    "https://meganorm.ru/Index2/1/4293722/4293722520.htm",
)


@dataclass(slots=True)
class CorpusRepairDocumentResult:
    """Summary of one repaired canonical document."""

    document_code: str
    document_version_id: str
    normalize_status: str
    deduplicated: bool
    indexed: bool


@dataclass(slots=True)
class CorpusRepairResult:
    """Summary of one targeted corpus repair run."""

    status: str
    placeholders_deleted: int
    repaired_documents: list[CorpusRepairDocumentResult]
    aliases_rebuilt: int
    retrieval_units_rebuilt: int
    embeddings_backfilled: int


def run_targeted_corpus_repair() -> dict[str, object]:
    """Repair known missing canonical documents and rebuild Stage 2A derived data."""

    repaired_results: list[CorpusRepairDocumentResult] = []
    placeholders_deleted = 0

    with session_scope() as session:
        placeholders_deleted = _delete_placeholder_documents(session)
        for card_url in _TARGET_CARD_URLS:
            repaired_results.append(_repair_document_from_card(session, card_url=card_url))

    aliases_rebuilt = 0
    retrieval_units_rebuilt = 0
    for item in repaired_results:
        with session_scope() as session:
            derived = rebuild_derived_retrieval_data(session, document_code=item.document_code)
            aliases_rebuilt += derived.aliases.aliases_created
            retrieval_units_rebuilt += derived.retrieval_units.units_created

    embedding_result = backfill_retrieval_unit_embeddings(max_units=None)
    result = CorpusRepairResult(
        status="ok",
        placeholders_deleted=placeholders_deleted,
        repaired_documents=repaired_results,
        aliases_rebuilt=aliases_rebuilt,
        retrieval_units_rebuilt=retrieval_units_rebuilt,
        embeddings_backfilled=embedding_result.processed_units,
    )
    return asdict(result)


def _delete_placeholder_documents(session) -> int:
    repository = DocumentRepository(session)
    deleted = 0
    for code in _PLACEHOLDER_CODES:
        document = repository.get_by_normalized_code(normalize_document_code(code))
        if document is None:
            continue
        logger.info("Deleting placeholder canonical document %s", document.display_code)
        session.delete(document)
        deleted += 1
    session.flush()
    return deleted


def _repair_document_from_card(session, *, card_url: str) -> CorpusRepairDocumentResult:
    card_html = fetch_document_card(card_url)
    card_data = parse_document_card(card_url, card_html, source_list_status_raw=None)
    persist_result = persist_document_card(
        session,
        card_data=card_data,
        list_page_url=None,
        seed_url=card_url,
        queue_download_job=False,
    )
    if persist_result.document_version_id is None:
        raise ValueError(f"Repair did not create a document version for {card_url}")

    version_id = persist_result.document_version_id
    download_document_artifacts(
        session,
        document_version_id=version_id,
        document_code=card_data.document_code,
        card_url=card_data.card_url,
        html_url=card_data.html_url,
        pdf_url=card_data.pdf_url,
        print_url=card_data.print_url,
        has_full_html=card_data.has_full_html,
        has_page_images=card_data.has_page_images,
        queue_next_job=False,
    )
    extract_result = extract_document_text(
        session,
        document_version_id=version_id,
        queue_next_job=False,
    )
    if extract_result.needs_ocr:
        run_document_ocr(
            session,
            document_version_id=version_id,
            queue_next_job=False,
        )
    normalize_result = normalize_document_structure(
        session,
        document_version_id=version_id,
        queue_next_job=False,
    )
    indexed = False
    if not normalize_result.deduplicated:
        index_document_version(session, document_version_id=version_id)
        indexed = True

    return CorpusRepairDocumentResult(
        document_code=normalize_document_code(card_data.document_code),
        document_version_id=version_id,
        normalize_status=normalize_result.status,
        deduplicated=normalize_result.deduplicated,
        indexed=indexed,
    )
