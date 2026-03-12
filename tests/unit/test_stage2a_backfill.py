from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from unittest.mock import MagicMock, patch

from qanorm.models import Document, DocumentAlias, DocumentNode, DocumentSource, DocumentVersion, RetrievalUnit
from qanorm.stage2a.config import Stage2AIndexingConfig
from qanorm.stage2a.indexing.aliases import build_document_alias_drafts, normalize_alias_value
from qanorm.stage2a.indexing.backfill import (
    AliasBackfillResult,
    GeminiEmbeddingClient,
    RetrievalUnitBackfillResult,
    backfill_derived_retrieval_data_worker,
    build_embedding_preflight_report,
    read_derived_backfill_state,
    read_embedding_backfill_state,
    start_parallel_embedding_backfill_processes,
    start_parallel_derived_backfill_processes,
    start_derived_backfill_process,
    start_embedding_backfill_process,
)
from qanorm.stage2a.indexing.units import build_retrieval_units, enrich_document_nodes


class _AllResult:
    def __init__(self, rows: list[tuple[str, list[float] | None]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, list[float] | None]]:
        return self._rows


def test_build_document_alias_drafts_generates_code_title_and_url_variants() -> None:
    document = Document(
        id=uuid4(),
        normalized_code="СП 20.13330.2016",
        display_code="СП 20.13330.2016",
        title="Нагрузки и воздействия. Основные требования.",
    )
    sources = [
        DocumentSource(
            document_id=document.id,
            document_version_id=uuid4(),
            card_url="https://docs.example.test/cards/sp-20",
            html_url="https://docs.example.test/html/sp-20",
            pdf_url="https://docs.example.test/pdf/sp-20.pdf",
            print_url="https://docs.example.test/print/sp-20",
        )
    ]

    aliases = build_document_alias_drafts(document, sources=sources)
    alias_values = {(alias.alias_type, alias.alias_normalized) for alias in aliases}

    assert ("display_code", "сп 20.13330.2016") in alias_values
    assert ("display_code", "sp 20.13330") in alias_values
    assert ("display_code", "sp 20") in alias_values
    assert ("title", "нагрузки и воздействия. основные требования.") in alias_values
    assert ("card_url", "docs.example.test/cards/sp-20") in alias_values


def test_normalize_alias_value_compacts_urls_and_text() -> None:
    assert normalize_alias_value(" https://Example.test/docs/SP-20/?v=1 ") == "example.test/docs/sp-20?v=1"
    assert normalize_alias_value("  СП 20.13330.2016 ") == "сп 20.13330.2016"


def test_build_retrieval_units_builds_document_card_and_semantic_blocks() -> None:
    document = Document(
        id=uuid4(),
        normalized_code="СП 20.13330.2016",
        display_code="СП 20.13330.2016",
        title="Нагрузки и воздействия",
    )
    version = DocumentVersion(id=uuid4(), document_id=document.id, is_active=True)
    title_id = uuid4()
    section_id = uuid4()
    point_id = uuid4()
    paragraph_id = uuid4()
    nodes = [
        DocumentNode(id=title_id, document_version_id=version.id, node_type="title", text=document.title or "", order_index=1),
        DocumentNode(
            id=section_id,
            document_version_id=version.id,
            parent_node_id=title_id,
            node_type="section",
            label="1",
            title="Общие положения",
            text="1 Общие положения",
            order_index=2,
        ),
        DocumentNode(
            id=point_id,
            document_version_id=version.id,
            parent_node_id=section_id,
            node_type="point",
            label="1.1",
            title="Нагрузки",
            text="1.1 Нагрузки",
            order_index=3,
        ),
        DocumentNode(
            id=paragraph_id,
            document_version_id=version.id,
            parent_node_id=point_id,
            node_type="paragraph",
            text="При проектировании следует учитывать постоянные и временные нагрузки.",
            order_index=4,
        ),
    ]
    aliases = [
        DocumentAlias(
            document_id=document.id,
            alias_raw="SP 20.13330",
            alias_normalized="sp 20.13330",
            alias_type="short_code",
            confidence=1.0,
        )
    ]
    config = Stage2AIndexingConfig(
        semantic_block_min_chars=40,
        semantic_block_target_chars=120,
        semantic_block_max_chars=240,
        semantic_block_max_nodes=8,
        document_card_max_headings=8,
        embed_batch_size=8,
    )

    updated_count = enrich_document_nodes(nodes)
    result = build_retrieval_units(document, version, nodes=nodes, aliases=aliases, config=config)

    assert updated_count >= 3
    assert "Алиасы: SP 20.13330" in result.document_card.text
    assert result.document_card.text_tsv is not None
    assert len(result.semantic_blocks) == 1
    assert result.semantic_blocks[0].locator_primary == "1.1"
    assert result.semantic_blocks[0].heading_path == "Нагрузки и воздействия > 1 Общие положения > 1.1 Нагрузки"
    assert result.semantic_blocks[0].text_tsv is not None


def test_build_embedding_preflight_report_estimates_pending_embeddings() -> None:
    session = MagicMock()
    session.execute.return_value = _AllResult(
        [
            ("Код документа: СП 20.13330.2016\nНазвание: Нагрузки", None),
            ("1.1 Нагрузки\nПостоянные нагрузки обязательны.", None),
            ("Уже встроено", [0.1, 0.2]),
        ]
    )

    report = build_embedding_preflight_report(session, price_per_million_tokens=0.2)

    assert report.total_units == 3
    assert report.pending_units == 2
    assert report.estimated_input_tokens > 0
    assert report.estimated_cost_usd is not None
    assert report.estimated_embedding_storage_bytes == 2 * 1536 * 4


def test_gemini_embedding_client_posts_batch_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers["x-goog-api-key"]
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]})

    with patch.dict(os.environ, {"QANORM_GEMINI_API_KEY": "test-key", "QANORM_GEMINI_API_BASE_URL": "https://unit.test"}):
        with GeminiEmbeddingClient(
            model="gemini-embedding-2-preview",
            transport=httpx.MockTransport(handler),
        ) as client:
            embeddings = client.embed_texts(["alpha", "beta"], task_type="RETRIEVAL_DOCUMENT")

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["api_key"] == "test-key"
    assert str(captured["url"]).endswith("/v1beta/models/gemini-embedding-2-preview:batchEmbedContents")
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert payload["requests"][0]["content"]["parts"][0]["text"] == "alpha"


def test_gemini_embedding_client_normalizes_versioned_base_url() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}]})

    with patch.dict(os.environ, {"QANORM_GEMINI_API_KEY": "test-key", "QANORM_GEMINI_API_BASE_URL": "https://unit.test/v1beta"}):
        with GeminiEmbeddingClient(
            model="gemini-embedding-2-preview",
            transport=httpx.MockTransport(handler),
        ) as client:
            client.embed_texts(["alpha"], task_type="RETRIEVAL_DOCUMENT")

    assert str(captured["url"]).endswith("/v1beta/models/gemini-embedding-2-preview:batchEmbedContents")


def test_start_embedding_backfill_process_spawns_detached_worker_and_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "backfill.log"

    with patch("qanorm.stage2a.indexing.backfill.subprocess.Popen", return_value=SimpleNamespace(pid=4321)) as popen_mock:
        result = start_embedding_backfill_process(state_path=state_path, log_path=log_path)

    assert result["status"] == "started"
    assert result["pid"] == 4321
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["status"] == "queued"
    assert payload["pid"] == 4321
    assert "stage2a-embed-backfill-worker" in payload["command"]
    popen_mock.assert_called_once()


def test_start_parallel_embedding_backfill_processes_spawns_workers_and_writes_manifest(tmp_path: Path) -> None:
    state_path = tmp_path / "embed-state.json"
    log_path = tmp_path / "embed.log"
    manifest_path = tmp_path / "embed-manifest.json"
    process_ids = iter([4321, 4322])

    with patch("qanorm.stage2a.indexing.backfill.subprocess.Popen", side_effect=lambda *args, **kwargs: SimpleNamespace(pid=next(process_ids))):
        result = start_parallel_embedding_backfill_processes(
            worker_count=2,
            state_path=state_path,
            log_path=log_path,
            manifest_path=manifest_path,
        )

    assert result.status == "started"
    assert result.worker_count == 2
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["worker_count"] == 2
    assert len(manifest["workers"]) == 2
    shard_one = json.loads((tmp_path / "embed-state.shard-01-of-02.json").read_text(encoding="utf-8"))
    shard_two = json.loads((tmp_path / "embed-state.shard-02-of-02.json").read_text(encoding="utf-8"))
    assert shard_one["worker_index"] == 0
    assert shard_two["worker_index"] == 1
    assert shard_one["worker_count"] == 2
    assert shard_two["worker_count"] == 2


def test_start_derived_backfill_process_spawns_detached_worker_and_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "derived-state.json"
    log_path = tmp_path / "derived.log"

    with patch("qanorm.stage2a.indexing.backfill.subprocess.Popen", return_value=SimpleNamespace(pid=9876)) as popen_mock:
        result = start_derived_backfill_process(
            document_code="SP 20.13330.2016",
            state_path=state_path,
            log_path=log_path,
        )

    assert result["status"] == "started"
    assert result["pid"] == 9876
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["status"] == "queued"
    assert payload["document_code"] == "SP 20.13330.2016"
    assert "stage2a-derived-backfill-worker" in payload["command"]
    popen_mock.assert_called_once()


def test_backfill_derived_retrieval_data_worker_resumes_from_checkpoint(tmp_path: Path) -> None:
    state_path = tmp_path / "derived-state.json"
    log_path = tmp_path / "derived.log"
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "document_code": None,
                "processed_documents": 1,
                "processed_document_codes": ["doc-a"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _FakeSession:
        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def commit(self) -> None:
            return None

        def expunge_all(self) -> None:
            return None

    processed_codes: list[str] = []

    def fake_rebuild(session, *, document_code=None):
        processed_codes.append(document_code)
        return SimpleNamespace(
            aliases=AliasBackfillResult(status="ok", documents_processed=1, aliases_deleted=0, aliases_created=2),
            retrieval_units=RetrievalUnitBackfillResult(
                status="ok",
                document_versions_processed=1,
                node_metadata_updated=3,
                units_deleted=0,
                units_created=4,
            ),
        )

    with patch("qanorm.stage2a.indexing.backfill._list_target_document_codes", return_value=["doc-a", "doc-b", "doc-c"]):
        with patch("qanorm.stage2a.indexing.backfill.create_session_factory", return_value=lambda: _FakeSession()):
            with patch("qanorm.stage2a.indexing.backfill.rebuild_derived_retrieval_data", side_effect=fake_rebuild):
                result = backfill_derived_retrieval_data_worker(state_path=state_path, log_path=log_path)

    assert result.status == "completed"
    assert result.processed_documents == 3
    assert result.remaining_documents == 0
    assert processed_codes == ["doc-b", "doc-c"]
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["processed_documents"] == 3
    assert payload["processed_document_codes"] == ["doc-a", "doc-b", "doc-c"]


def test_start_parallel_derived_backfill_processes_seeds_shard_states(tmp_path: Path) -> None:
    state_path = tmp_path / "derived-state.json"
    log_path = tmp_path / "derived.log"
    manifest_path = tmp_path / "derived-manifest.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "document_code": None,
                "processed_document_codes": ["doc-a", "doc-c"],
                "processed_documents": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    process_ids = iter([1111, 2222])

    with patch("qanorm.stage2a.indexing.backfill._list_target_document_codes", return_value=["doc-a", "doc-b", "doc-c", "doc-d"]):
        with patch("qanorm.stage2a.indexing.backfill.subprocess.Popen", side_effect=lambda *args, **kwargs: SimpleNamespace(pid=next(process_ids))):
            result = start_parallel_derived_backfill_processes(
                worker_count=2,
                state_path=state_path,
                log_path=log_path,
                manifest_path=manifest_path,
            )

    assert result.status == "started"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["worker_count"] == 2
    assert len(manifest["workers"]) == 2
    shard_one = json.loads((tmp_path / "derived-state.shard-01-of-02.json").read_text(encoding="utf-8"))
    shard_two = json.loads((tmp_path / "derived-state.shard-02-of-02.json").read_text(encoding="utf-8"))
    assert shard_one["processed_document_codes"] == ["doc-a", "doc-c"]
    assert shard_two["processed_document_codes"] == []


def test_read_derived_backfill_state_aggregates_parallel_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "derived-manifest.json"
    shard_one_state_path = tmp_path / "derived-state.shard-01-of-02.json"
    shard_two_state_path = tmp_path / "derived-state.shard-02-of-02.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "running",
                "worker_count": 2,
                "manifest_path": str(manifest_path),
                "workers": [
                    {"state_path": str(shard_one_state_path)},
                    {"state_path": str(shard_two_state_path)},
                ],
                "created_at": "2026-03-11T14:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shard_one_state_path.write_text(
        json.dumps({"status": "running", "target_documents": 10, "processed_documents": 4, "remaining_documents": 6}, ensure_ascii=False),
        encoding="utf-8",
    )
    shard_two_state_path.write_text(
        json.dumps({"status": "completed", "target_documents": 8, "processed_documents": 8, "remaining_documents": 0}, ensure_ascii=False),
        encoding="utf-8",
    )

    aggregated = read_derived_backfill_state(manifest_path=manifest_path)

    assert aggregated["status"] == "running"
    assert aggregated["worker_count"] == 2
    assert aggregated["target_documents"] == 18
    assert aggregated["processed_documents"] == 12
    assert aggregated["remaining_documents"] == 6


def test_read_embedding_backfill_state_aggregates_parallel_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "embed-manifest.json"
    shard_one_state_path = tmp_path / "embed-state.shard-01-of-02.json"
    shard_two_state_path = tmp_path / "embed-state.shard-02-of-02.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "running",
                "worker_count": 2,
                "manifest_path": str(manifest_path),
                "workers": [
                    {"state_path": str(shard_one_state_path)},
                    {"state_path": str(shard_two_state_path)},
                ],
                "created_at": "2026-03-11T15:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shard_one_state_path.write_text(
        json.dumps({"status": "running", "processed_units": 120, "remaining_units": 1800}, ensure_ascii=False),
        encoding="utf-8",
    )
    shard_two_state_path.write_text(
        json.dumps({"status": "completed", "processed_units": 140, "remaining_units": 1720}, ensure_ascii=False),
        encoding="utf-8",
    )

    aggregated = read_embedding_backfill_state(manifest_path=manifest_path)

    assert aggregated["status"] == "running"
    assert aggregated["worker_count"] == 2
    assert aggregated["processed_units"] == 260
    assert aggregated["remaining_units"] == 1720


def test_build_document_alias_drafts_generates_compact_code_variants() -> None:
    document = Document(
        id=uuid4(),
        normalized_code="SP 63.13330.2018",
        display_code="SP 63.13330.2018",
        title="Concrete structures",
    )

    aliases = build_document_alias_drafts(document, sources=[])
    alias_values = {alias.alias_normalized for alias in aliases}

    assert "sp63.13330.2018" in alias_values
    assert "sp63.13330" in alias_values
    assert "sp63" in alias_values


def test_normalize_alias_value_keeps_compact_codes() -> None:
    assert normalize_alias_value("SP63") == "sp63"
