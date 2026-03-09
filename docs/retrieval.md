# Retrieval in QANorm

## 1. Retrieval Goals

The retrieval layer in QANorm must:

- find relevant regulatory fragments quickly;
- preserve precise locators and quotes;
- support exact, lexical, and dense retrieval;
- minimize storage and embedding cost;
- keep structural normalization separate from search-oriented data.

## 2. Core Entities

### `document_nodes`

These store:

- document structure;
- hierarchy;
- `label`, `title`, `text`;
- ordering and locators.

They are used for:

- reconstructing exact quotes;
- linking retrieval hits back to documents and locators;
- building structural breadcrumbs.

### `retrieval_chunks`

This is the main search unit for Stage 2.

Each chunk stores:

- document and version IDs;
- `start_node_id`, `end_node_id`;
- `chunk_index`;
- `heading_path`;
- `locator` and `locator_end`;
- `chunk_text`;
- `chunk_hash`;
- `char_count`, `token_count`;
- `is_active`.

### `chunk_embeddings`

This is the dense layer for retrieval chunks.

Each row stores:

- `chunk_hash`;
- `model_provider`;
- `model_name`;
- `model_revision`;
- `dimensions`;
- `chunk_text_sample`;
- `embedding`.

Embeddings are deduplicated by `chunk_hash`.

## 3. How Chunks Are Built

Chunking is implemented in:

- `src/qanorm/services/qa/chunking_service.py`

Main rules:

- headings (`title`, `section`, `subsection`, `appendix`) are treated as context;
- retrieval anchors are built at the main normative clause level;
- `subpoint` stays attached to its parent `point`;
- short neighboring groups with the same heading path are merged;
- overlap is minimal.

Current thresholds:

- `min_tokens = 40`
- `max_tokens = 220`

This balances:

- enough context for semantic retrieval;
- lower chunk count;
- lower embedding cost;
- better dense retrieval quality than line-level splitting.

## 4. How `retrieval_chunks` Backfill Works

Chunks are derived from active `document_versions`.

Key functions:

- `sync_retrieval_chunks_for_version(...)`
- `backfill_active_retrieval_chunks(...)`

Flow:

1. Load one active document version.
2. Load its `document_nodes`.
3. Build retrieval chunk drafts.
4. Delete previous chunks for that version.
5. Persist the new chunks into `retrieval_chunks`.

## 5. How Embedding Backfill Works

Backfill is implemented in:

- `src/qanorm/services/qa/retrieval_service.py`
- `scripts/backfill_chunk_embeddings.py`

Main rules:

- embeddings are generated only for the active chunk corpus;
- `chunk_hash` is used for deduplication;
- existing embeddings are reused;
- the workflow is resumable;
- checkpoint commits make restart and recovery safe.

The embedding provider is selected through runtime configuration. The current production path is documented in `configuration.md`.

## 6. Why the Dense Corpus Is Cheaper than Node-Level Embeddings

The cost reduction comes from three decisions:

1. No embeddings on `document_nodes`.
2. Dense storage only for the active corpus.
3. Reuse of identical chunk embeddings through `chunk_hash`.

This reduces:

- database size;
- vector index size;
- full-corpus embedding cost;
- model-switch re-embedding cost.

## 7. Retrieval Pipeline

Normative retrieval is built from:

1. `exact match`
   - search by document code and locator

2. `FTS`
   - search over `chunk_text_tsv`

3. `vector search`
   - search over `chunk_embeddings`

4. `fusion`
   - reciprocal-rank fusion across exact, FTS, and vector results

5. `secondary hits`
   - related documents loaded through `document_references`

The resulting `RetrievalHit` already includes:

- `chunk_id`
- `document_id`
- `document_version_id`
- `locator`
- `locator_end`
- `chunk_text`
- exact `quote`
- `freshness_status`

## 8. Exact Quote Reconstruction

Dense retrieval runs on chunk data, but exact citations are reconstructed from `document_nodes`.

Each retrieval hit contains:

- `start_node_id`
- `end_node_id`

The service then:

1. loads the node span between them;
2. reconstructs the precise text;
3. produces the final `quote`.

This keeps retrieval efficient while preserving citation precision.

## 9. Operational Commands

Example resumable embedding backfill:

```powershell
.\\.venv\\Scripts\\python.exe -u scripts\\backfill_chunk_embeddings.py `
  --batch-size 48 `
  --checkpoint-every-batches 50 `
  --generation-batches-per-run 100 `
  --request-timeout-seconds 90
```

Example log monitoring:

```powershell
Get-Content -Wait data\\logs\\chunk_embeddings_backfill_fast.log
```

Example database progress check:

```powershell
docker exec qanorm-pg16 psql -U postgres -d qanorm -c "select count(*) as saved, 174716 - count(*) as remaining from chunk_embeddings;"
```

## 10. Expected Retrieval Behavior

The retrieval layer is expected to provide:

- precise code and locator lookup;
- high-quality lexical retrieval;
- dense retrieval over deduplicated chunks;
- correct normative evidence persistence;
- citations suitable for user-facing answers and verification.

