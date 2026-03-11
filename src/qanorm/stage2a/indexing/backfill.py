"""Backfill services for Stage 2A derived retrieval data."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from qanorm.db.session import create_session_factory, session_scope
from qanorm.models import Document, DocumentSource, DocumentVersion, RetrievalUnit
from qanorm.normalizers.codes import normalize_document_code
from qanorm.observability.metrics import increment_event, set_backfill_metric
from qanorm.repositories import DocumentAliasRepository, DocumentNodeRepository, DocumentRepository, DocumentSourceRepository, RetrievalUnitRepository
from qanorm.settings import PROJECT_ROOT, get_settings
from qanorm.stage2a.config import get_stage2a_config
from qanorm.stage2a.indexing.aliases import build_document_alias_models
from qanorm.stage2a.indexing.units import build_retrieval_units, enrich_document_nodes
from qanorm.utils.text import normalize_whitespace


_DEFAULT_EMBEDDING_PRICE_BY_MODEL = {
    "gemini-embedding-001": 0.15,
    "gemini-embedding-2-preview": 0.20,
}


@dataclass(slots=True)
class AliasBackfillResult:
    """Summary of a document-alias backfill run."""

    status: str
    documents_processed: int
    aliases_deleted: int
    aliases_created: int


@dataclass(slots=True)
class RetrievalUnitBackfillResult:
    """Summary of a retrieval-unit rebuild run."""

    status: str
    document_versions_processed: int
    node_metadata_updated: int
    units_deleted: int
    units_created: int


@dataclass(slots=True)
class DerivedRetrievalDataResult:
    """Summary of the full derived-data rebuild."""

    status: str
    aliases: AliasBackfillResult
    retrieval_units: RetrievalUnitBackfillResult


@dataclass(slots=True)
class EmbeddingPreflightReport:
    """Estimated embedding workload for retrieval-unit backfill."""

    status: str
    model: str
    pending_units: int
    total_units: int
    estimated_input_tokens: int
    estimated_cost_usd: float | None
    price_per_million_tokens_usd: float | None
    estimated_embedding_storage_bytes: int
    estimated_embedding_storage_human: str
    output_dimensionality: int
    average_chars_per_unit: int


@dataclass(slots=True)
class EmbeddingBackfillResult:
    """Summary of an embedding backfill run."""

    status: str
    processed_units: int
    remaining_units: int
    state_path: str
    log_path: str


@dataclass(slots=True)
class DerivedBackfillResult:
    """Summary of a detached derived-data backfill run."""

    status: str
    processed_documents: int
    remaining_documents: int
    state_path: str
    log_path: str


class GeminiEmbeddingClient:
    """Thin Gemini embeddings client over raw HTTP."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout_seconds: int | None = None,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config = get_stage2a_config()
        base_url = os.environ.get(config.provider.api.base_url_env, "https://generativelanguage.googleapis.com")
        api_key = os.environ.get(config.provider.api.api_key_env)
        if not api_key:
            raise ValueError(f"Environment variable '{config.provider.api.api_key_env}' is required for embeddings")

        self.model = model or config.models.embeddings
        self.output_dimensionality = config.embeddings.output_dimensionality
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds or 90,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close the underlying HTTP client when owned."""

        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "GeminiEmbeddingClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def embed_texts(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        """Embed one batch of retrieval-unit texts."""

        if not texts:
            return []

        response = self.client.post(
            f"/v1beta/models/{self.model}:batchEmbedContents",
            headers={"x-goog-api-key": self.api_key},
            json={
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": task_type,
                        "outputDimensionality": self.output_dimensionality,
                    }
                    for text in texts
                ]
            },
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise ValueError(f"Expected {len(texts)} embeddings, got {len(embeddings)}")
        return [list(item["values"]) for item in embeddings]


def backfill_document_aliases(session: Session, *, document_code: str | None = None) -> AliasBackfillResult:
    """Rebuild aliases for one or all canonical documents."""

    document_repository = DocumentRepository(session)
    source_repository = DocumentSourceRepository(session)
    alias_repository = DocumentAliasRepository(session)
    documents = _list_target_documents(session, document_code=document_code)

    aliases_deleted = 0
    aliases_created = 0
    for document in documents:
        sources = source_repository.list_for_document(document.id)
        aliases_deleted += alias_repository.delete_for_document(document.id)
        alias_models = build_document_alias_models(document, sources=sources)
        if alias_models:
            alias_repository.add_many(alias_models)
        aliases_created += len(alias_models)

    session.flush()
    return AliasBackfillResult(
        status="ok",
        documents_processed=len(documents),
        aliases_deleted=aliases_deleted,
        aliases_created=aliases_created,
    )


def backfill_retrieval_units(session: Session, *, document_code: str | None = None) -> RetrievalUnitBackfillResult:
    """Rebuild retrieval units for active document versions."""

    alias_repository = DocumentAliasRepository(session)
    node_repository = DocumentNodeRepository(session)
    unit_repository = RetrievalUnitRepository(session)
    documents = _list_target_documents(session, document_code=document_code)

    processed_versions = 0
    node_metadata_updated = 0
    units_deleted = 0
    units_created = 0

    for document in documents:
        versions = list(document.versions)
        for version in versions:
            deleted_for_version = unit_repository.delete_for_document_version(version.id)
            units_deleted += deleted_for_version

            if document.current_version_id != version.id or not version.is_active:
                continue

            nodes = node_repository.list_for_document_version(version.id)
            node_metadata_updated += enrich_document_nodes(nodes)
            aliases = alias_repository.list_for_document(document.id)
            if not aliases:
                aliases = build_document_alias_models(document, sources=document.sources)

            build_result = build_retrieval_units(
                document,
                version,
                nodes=nodes,
                aliases=aliases,
                config=get_stage2a_config().indexing,
            )
            created_units = [build_result.document_card, *build_result.semantic_blocks]
            if created_units:
                unit_repository.add_many(created_units)
            processed_versions += 1
            units_created += len(created_units)

    session.flush()
    return RetrievalUnitBackfillResult(
        status="ok",
        document_versions_processed=processed_versions,
        node_metadata_updated=node_metadata_updated,
        units_deleted=units_deleted,
        units_created=units_created,
    )


def rebuild_derived_retrieval_data(session: Session, *, document_code: str | None = None) -> DerivedRetrievalDataResult:
    """Rebuild aliases and retrieval units in one deterministic pass."""

    alias_result = backfill_document_aliases(session, document_code=document_code)
    retrieval_unit_result = backfill_retrieval_units(session, document_code=document_code)
    return DerivedRetrievalDataResult(status="ok", aliases=alias_result, retrieval_units=retrieval_unit_result)


def build_embedding_preflight_report(
    session: Session,
    *,
    price_per_million_tokens: float | None = None,
) -> EmbeddingPreflightReport:
    """Estimate pending embedding workload and storage."""

    config = get_stage2a_config()
    rows = list(session.execute(select(RetrievalUnit.text, RetrievalUnit.embedding)).all())
    total_units = len(rows)
    pending_texts = [text for text, embedding in rows if embedding is None]
    pending_units = len(pending_texts)
    estimated_tokens = sum(_estimate_tokens(text, chars_per_token=config.embeddings.average_chars_per_token) for text in pending_texts)
    resolved_price = price_per_million_tokens
    if resolved_price is None:
        resolved_price = config.embeddings.estimated_text_input_price_per_million_tokens
    if resolved_price is None:
        resolved_price = _DEFAULT_EMBEDDING_PRICE_BY_MODEL.get(config.models.embeddings)
    estimated_cost = None
    if resolved_price is not None:
        estimated_cost = round((estimated_tokens / 1_000_000) * resolved_price, 4)

    embedding_bytes = pending_units * config.embeddings.output_dimensionality * 4
    average_chars = 0 if pending_units == 0 else round(sum(len(text) for text in pending_texts) / pending_units)
    return EmbeddingPreflightReport(
        status="ok",
        model=config.models.embeddings,
        pending_units=pending_units,
        total_units=total_units,
        estimated_input_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost,
        price_per_million_tokens_usd=resolved_price,
        estimated_embedding_storage_bytes=embedding_bytes,
        estimated_embedding_storage_human=_format_bytes(embedding_bytes),
        output_dimensionality=config.embeddings.output_dimensionality,
        average_chars_per_unit=average_chars,
    )


def backfill_retrieval_unit_embeddings(
    *,
    state_path: str | Path | None = None,
    log_path: str | Path | None = None,
    max_units: int | None = None,
) -> EmbeddingBackfillResult:
    """Run or resume retrieval-unit embedding backfill in the current process."""

    config = get_stage2a_config()
    resolved_state_path, resolved_log_path = _resolve_embedding_paths(state_path=state_path, log_path=log_path)
    logger = _build_backfill_logger(resolved_log_path, logger_name="embedding_backfill")
    start_time = datetime.now(UTC)
    processed_units = 0
    pending_units = 0

    state = _read_state_file(resolved_state_path)
    if state.get("started_at") is None:
        state["started_at"] = start_time.isoformat()

    logger.info("Starting retrieval-unit embedding backfill")
    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": "running",
            "pid": os.getpid(),
            "model": config.models.embeddings,
            "updated_at": start_time.isoformat(),
            "log_path": str(resolved_log_path),
        },
    )

    try:
        with GeminiEmbeddingClient(model=config.models.embeddings) as client:
            while True:
                batch_size = config.indexing.embed_batch_size
                if max_units is not None:
                    remaining_quota = max_units - processed_units
                    if remaining_quota <= 0:
                        break
                    batch_size = min(batch_size, remaining_quota)

                with session_scope() as session:
                    repository = RetrievalUnitRepository(session)
                    batch = repository.list_pending_embeddings(limit=batch_size)
                    pending_units = repository.count_embeddings_pending()
                    if not batch:
                        break

                    texts = [unit.text for unit in batch]
                    embeddings = client.embed_texts(texts, task_type=config.embeddings.document_task_type)
                    for unit, embedding in zip(batch, embeddings, strict=True):
                        unit.embedding = embedding
                    session.flush()
                    processed_units += len(batch)
                    pending_units = max(0, pending_units - len(batch))
                    last_unit_id = str(batch[-1].id)

                logger.info(
                    "Embedded %s retrieval units; remaining=%s last_unit_id=%s",
                    len(batch),
                    pending_units,
                    last_unit_id,
                )
                set_backfill_metric("processed_units", processed_units)
                set_backfill_metric("pending_units", pending_units)
                _write_state_file(
                    resolved_state_path,
                    {
                        **state,
                        "status": "running",
                        "pid": os.getpid(),
                        "processed_units": processed_units,
                        "remaining_units": pending_units,
                        "last_unit_id": last_unit_id,
                        "updated_at": datetime.now(UTC).isoformat(),
                        "log_path": str(resolved_log_path),
                    },
                )

        increment_event("stage2a_embedding_backfill", status="ok")
        final_status = "completed"
        logger.info("Embedding backfill finished; processed_units=%s remaining_units=%s", processed_units, pending_units)
    except Exception as exc:
        increment_event("stage2a_embedding_backfill", status="failed")
        logger.exception("Embedding backfill failed")
        _write_state_file(
            resolved_state_path,
            {
                **state,
                "status": "failed",
                "pid": os.getpid(),
                "processed_units": processed_units,
                "remaining_units": pending_units,
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(UTC).isoformat(),
                "log_path": str(resolved_log_path),
            },
        )
        raise

    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": final_status,
            "pid": os.getpid(),
            "processed_units": processed_units,
            "remaining_units": pending_units,
            "completed_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "log_path": str(resolved_log_path),
        },
    )
    return EmbeddingBackfillResult(
        status=final_status,
        processed_units=processed_units,
        remaining_units=pending_units,
        state_path=str(resolved_state_path),
        log_path=str(resolved_log_path),
    )


def start_embedding_backfill_process(
    *,
    state_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Spawn a detached resumable embedding backfill worker."""

    resolved_state_path, resolved_log_path = _resolve_embedding_paths(state_path=state_path, log_path=log_path)
    command = [
        sys.executable,
        "-m",
        "qanorm.cli.main",
        "stage2a-embed-backfill-worker",
        "--state-path",
        str(resolved_state_path),
        "--log-path",
        str(resolved_log_path),
    ]

    popen_kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    _write_state_file(
        resolved_state_path,
        {
            "status": "queued",
            "pid": process.pid,
            "command": command,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "log_path": str(resolved_log_path),
        },
    )
    return {
        "status": "started",
        "pid": process.pid,
        "state_path": str(resolved_state_path),
        "log_path": str(resolved_log_path),
    }


def backfill_derived_retrieval_data_worker(
    *,
    document_code: str | None = None,
    state_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> DerivedBackfillResult:
    """Run or resume derived retrieval-data rebuild in the current process."""

    resolved_state_path, resolved_log_path = _resolve_derived_paths(state_path=state_path, log_path=log_path)
    logger = _build_backfill_logger(resolved_log_path, logger_name="derived_backfill")
    start_time = datetime.now(UTC)
    state = _read_state_file(resolved_state_path)
    target_codes = _list_target_document_codes(document_code=document_code)
    same_target = state.get("document_code") == document_code
    processed_codes = set(state.get("processed_document_codes") or []) if same_target else set()
    processed_documents = int(state.get("processed_documents", 0)) if same_target else 0
    logger.info("Starting derived retrieval-data rebuild; target_documents=%s", len(target_codes))
    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": "running",
            "pid": os.getpid(),
            "document_code": document_code,
            "target_documents": len(target_codes),
            "processed_documents": processed_documents,
            "processed_document_codes": sorted(processed_codes),
            "started_at": state.get("started_at") or start_time.isoformat(),
            "updated_at": start_time.isoformat(),
            "log_path": str(resolved_log_path),
        },
    )

    try:
        session_factory = create_session_factory()
        for normalized_code in target_codes:
            if normalized_code in processed_codes:
                continue

            with session_factory() as session:
                result = rebuild_derived_retrieval_data(session, document_code=normalized_code)
                session.commit()
                session.expunge_all()

            processed_codes.add(normalized_code)
            processed_documents += 1
            remaining_documents = max(0, len(target_codes) - processed_documents)
            logger.info(
                "Rebuilt derived retrieval data for %s; remaining=%s aliases_created=%s units_created=%s",
                normalized_code,
                remaining_documents,
                result.aliases.aliases_created,
                result.retrieval_units.units_created,
            )
            set_backfill_metric("derived_processed_documents", processed_documents)
            set_backfill_metric("derived_remaining_documents", remaining_documents)
            _write_state_file(
                resolved_state_path,
                {
                    **state,
                    "status": "running",
                    "pid": os.getpid(),
                    "document_code": document_code,
                    "target_documents": len(target_codes),
                    "processed_documents": processed_documents,
                    "remaining_documents": remaining_documents,
                    "last_document_code": normalized_code,
                    "processed_document_codes": sorted(processed_codes),
                    "updated_at": datetime.now(UTC).isoformat(),
                    "log_path": str(resolved_log_path),
                },
            )

        increment_event("stage2a_derived_backfill", status="ok")
        final_status = "completed"
        remaining_documents = max(0, len(target_codes) - processed_documents)
        logger.info(
            "Derived retrieval-data rebuild finished; processed_documents=%s remaining_documents=%s",
            processed_documents,
            remaining_documents,
        )
    except Exception as exc:
        increment_event("stage2a_derived_backfill", status="failed")
        logger.exception("Derived retrieval-data rebuild failed")
        remaining_documents = max(0, len(target_codes) - processed_documents)
        _write_state_file(
            resolved_state_path,
            {
                **state,
                "status": "failed",
                "pid": os.getpid(),
                "document_code": document_code,
                "target_documents": len(target_codes),
                "processed_documents": processed_documents,
                "remaining_documents": remaining_documents,
                "processed_document_codes": sorted(processed_codes),
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(UTC).isoformat(),
                "log_path": str(resolved_log_path),
            },
        )
        raise

    _write_state_file(
        resolved_state_path,
        {
            **state,
            "status": final_status,
            "pid": os.getpid(),
            "document_code": document_code,
            "target_documents": len(target_codes),
            "processed_documents": processed_documents,
            "remaining_documents": remaining_documents,
            "processed_document_codes": sorted(processed_codes),
            "completed_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "log_path": str(resolved_log_path),
        },
    )
    return DerivedBackfillResult(
        status=final_status,
        processed_documents=processed_documents,
        remaining_documents=remaining_documents,
        state_path=str(resolved_state_path),
        log_path=str(resolved_log_path),
    )


def start_derived_backfill_process(
    *,
    document_code: str | None = None,
    state_path: str | Path | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Spawn a detached resumable worker for derived retrieval-data rebuild."""

    resolved_state_path, resolved_log_path = _resolve_derived_paths(state_path=state_path, log_path=log_path)
    command = [
        sys.executable,
        "-m",
        "qanorm.cli.main",
        "stage2a-derived-backfill-worker",
        "--state-path",
        str(resolved_state_path),
        "--log-path",
        str(resolved_log_path),
    ]
    if document_code is not None:
        command.extend(["--document-code", document_code])

    popen_kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    _write_state_file(
        resolved_state_path,
        {
            "status": "queued",
            "pid": process.pid,
            "document_code": document_code,
            "command": command,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "log_path": str(resolved_log_path),
        },
    )
    return {
        "status": "started",
        "pid": process.pid,
        "state_path": str(resolved_state_path),
        "log_path": str(resolved_log_path),
    }


def read_derived_backfill_state(*, state_path: str | Path | None = None, log_path: str | Path | None = None) -> dict[str, Any]:
    """Read the persisted state of the derived retrieval-data worker."""

    resolved_state_path, _ = _resolve_derived_paths(state_path=state_path, log_path=log_path)
    return _read_state_file(resolved_state_path)


def read_embedding_backfill_state(*, state_path: str | Path | None = None, log_path: str | Path | None = None) -> dict[str, Any]:
    """Read the persisted state of the embedding backfill worker."""

    resolved_state_path, _ = _resolve_embedding_paths(state_path=state_path, log_path=log_path)
    return _read_state_file(resolved_state_path)


def _list_target_documents(session: Session, *, document_code: str | None) -> list[Document]:
    if document_code is None:
        stmt = select(Document).order_by(Document.created_at.asc())
        return list(session.execute(stmt).scalars().all())

    normalized_code = normalize_document_code(document_code)
    document = DocumentRepository(session).get_by_normalized_code(normalized_code)
    if document is None:
        return []
    return [document]


def _estimate_tokens(text: str, *, chars_per_token: float) -> int:
    normalized = normalize_whitespace(text)
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / chars_per_token))


def _format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.1f} MB"
    return f"{size_bytes / (1024**3):.2f} GB"


def _resolve_embedding_paths(
    *,
    state_path: str | Path | None,
    log_path: str | Path | None,
) -> tuple[Path, Path]:
    return _resolve_backfill_paths(
        default_state_name="embedding_backfill_state.json",
        default_log_name="embedding_backfill.log",
        state_path=state_path,
        log_path=log_path,
    )


def _resolve_derived_paths(
    *,
    state_path: str | Path | None,
    log_path: str | Path | None,
) -> tuple[Path, Path]:
    return _resolve_backfill_paths(
        default_state_name="derived_backfill_state.json",
        default_log_name="derived_backfill.log",
        state_path=state_path,
        log_path=log_path,
    )


def _resolve_backfill_paths(
    *,
    default_state_name: str,
    default_log_name: str,
    state_path: str | Path | None,
    log_path: str | Path | None,
) -> tuple[Path, Path]:
    base_dir = get_settings().env.raw_storage_path.parent / "stage2a"
    resolved_state_path = Path(state_path) if state_path is not None else base_dir / default_state_name
    resolved_log_path = Path(log_path) if log_path is not None else base_dir / default_log_name
    resolved_state_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_state_path, resolved_log_path


def _build_backfill_logger(log_path: Path, *, logger_name: str) -> logging.Logger:
    logger = logging.getLogger(f"qanorm.stage2a.{logger_name}.{log_path}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def _read_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_rebuild_derived_retrieval_data(*, document_code: str | None = None) -> dict[str, Any]:
    """Run derived-data rebuild in a managed session."""

    alias_totals = AliasBackfillResult(status="ok", documents_processed=0, aliases_deleted=0, aliases_created=0)
    unit_totals = RetrievalUnitBackfillResult(
        status="ok",
        document_versions_processed=0,
        node_metadata_updated=0,
        units_deleted=0,
        units_created=0,
    )

    session_factory = create_session_factory()
    with session_factory() as session:
        for normalized_code in _list_target_document_codes(document_code=document_code):
            result = rebuild_derived_retrieval_data(session, document_code=normalized_code)
            session.commit()
            session.expunge_all()
            alias_totals.documents_processed += result.aliases.documents_processed
            alias_totals.aliases_deleted += result.aliases.aliases_deleted
            alias_totals.aliases_created += result.aliases.aliases_created
            unit_totals.document_versions_processed += result.retrieval_units.document_versions_processed
            unit_totals.node_metadata_updated += result.retrieval_units.node_metadata_updated
            unit_totals.units_deleted += result.retrieval_units.units_deleted
            unit_totals.units_created += result.retrieval_units.units_created

    return _dataclass_to_dict(
        DerivedRetrievalDataResult(
            status="ok",
            aliases=alias_totals,
            retrieval_units=unit_totals,
        )
    )


def run_document_alias_backfill(*, document_code: str | None = None) -> dict[str, Any]:
    """Run alias backfill in a managed session."""

    totals = AliasBackfillResult(status="ok", documents_processed=0, aliases_deleted=0, aliases_created=0)
    session_factory = create_session_factory()
    with session_factory() as session:
        for normalized_code in _list_target_document_codes(document_code=document_code):
            result = backfill_document_aliases(session, document_code=normalized_code)
            session.commit()
            session.expunge_all()
            totals.documents_processed += result.documents_processed
            totals.aliases_deleted += result.aliases_deleted
            totals.aliases_created += result.aliases_created
    return asdict(totals)


def run_retrieval_unit_backfill(*, document_code: str | None = None) -> dict[str, Any]:
    """Run retrieval-unit rebuild in a managed session."""

    totals = RetrievalUnitBackfillResult(
        status="ok",
        document_versions_processed=0,
        node_metadata_updated=0,
        units_deleted=0,
        units_created=0,
    )
    session_factory = create_session_factory()
    with session_factory() as session:
        for normalized_code in _list_target_document_codes(document_code=document_code):
            result = backfill_retrieval_units(session, document_code=normalized_code)
            session.commit()
            session.expunge_all()
            totals.document_versions_processed += result.document_versions_processed
            totals.node_metadata_updated += result.node_metadata_updated
            totals.units_deleted += result.units_deleted
            totals.units_created += result.units_created
    return asdict(totals)


def run_embedding_preflight(*, price_per_million_tokens: float | None = None) -> dict[str, Any]:
    """Run embedding preflight in a managed session."""

    with session_scope() as session:
        result = build_embedding_preflight_report(session, price_per_million_tokens=price_per_million_tokens)
    return asdict(result)


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    if "aliases" in payload:
        payload["aliases"] = asdict(value.aliases)
    if "retrieval_units" in payload:
        payload["retrieval_units"] = asdict(value.retrieval_units)
    return payload


def _list_target_document_codes(*, document_code: str | None) -> list[str | None]:
    if document_code is not None:
        return [normalize_document_code(document_code)]

    with session_scope() as session:
        stmt = select(Document.normalized_code).order_by(Document.created_at.asc())
        return list(session.execute(stmt).scalars().all())
