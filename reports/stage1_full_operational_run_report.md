# Stage 1 Full Operational Run Report

Date: 2026-03-06

## Scope

This report captures the full end-to-end Stage 1 operational run on the working PostgreSQL 16 + pgvector database after draining the ingestion queue to completion.

## Queue outcome

- `ingestion_jobs.completed = 19166`
- `ingestion_jobs.failed = 0`
- `ingestion_jobs.pending = 0`
- `ingestion_jobs.running = 0`

Completed jobs by type:

- `crawl_seed = 4`
- `parse_list_page = 16`
- `process_document_card = 11568`
- `download_artifacts = 1895`
- `extract_text = 1895`
- `normalize_document = 1895`
- `index_document = 1893`

## Persisted data

- `documents = 1681`
- `document_versions = 1895`
- `document_nodes = 4960734`
- `raw_artifacts = 16768`

Raw storage verification:

- The application-resolved `raw_storage_path` exists on disk.
- All `16768` rows from `raw_artifacts` were checked against `storage_path`.
- Missing files: `0`
- The storage root contains `16773` files total, which means there are `5` extra non-artifact files outside the tracked `raw_artifacts` set.

## Metrics snapshot

From `ingestion-metrics`:

- `documents_total = 1681`
- `documents_active = 1681`
- `documents_inactive = 0`
- `documents_with_raw_artifacts = 1681`
- `documents_with_extracted_text = 1681`
- `documents_with_ocr = 0`
- `documents_with_low_confidence_parse = 0`
- `documents_structured = 1681`
- `documents_indexed = 1681`
- `inactive_documents_in_active_index = 0`
- `active_documents_without_active_version = 0`
- `active_versions_without_text_source = 0`

Operational rates:

- `list_pages_success_rate = 1.0`
- `active_documents_reaching_card_rate = 1.0`
- `active_documents_with_raw_rate = 1.0`
- `active_documents_with_text_rate = 1.0`
- `active_documents_structured_rate = 1.0`
- `active_index_documents_with_correct_status_rate = 1.0`

Update tracking:

- `updates_detected = 2`
- `updates_successful = 2`
- `updates_failed = 0`

## Target comparison

`ingestion-report` returned `passed = true` for the Stage 1 target comparison from `Plan.md`.

Checks passed:

- list page success rate
- active documents reaching card stage
- active documents with raw artifacts
- active documents with extracted text
- active documents reaching structured form
- active index contains only correctly-statused documents
- inactive documents in active index equals zero

## Consistency checks

- Documents not reaching indexing: none
- Failed ingestion jobs: none
- Inactive documents present in active index: none
- Active documents without active version: none
- Active versions without extracted text or OCR evidence: none

## Outcome

Stage 1 full operational run completed successfully.

The local normative document base is populated, internally consistent, and retrieval-ready at the Stage 1 level.

Stage 2 can proceed based on this dataset snapshot.
