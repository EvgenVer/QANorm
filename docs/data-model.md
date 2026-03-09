# QANorm Data Model

## 1. Overall Structure

The data model is split into two layers:

- `Stage 1`  
  Canonical regulatory corpus and ingestion trail.

- `Stage 2`  
  Retrieval, sessions, answers, evidence, audit, and background checks.

It is also useful to distinguish between:

- `canonical data`  
  Source-of-truth entities such as `documents`, `document_versions`, and `document_nodes`

- `derived data`  
  Search or runtime-oriented entities such as `retrieval_chunks`, `chunk_embeddings`, and `qa_evidence`

## 2. Stage 1 Core Tables

- `documents`  
  Canonical registry of normative documents.

- `document_versions`  
  Versioned document states and edition metadata.

- `document_nodes`  
  Canonical structural representation of normalized document content.

- `document_references`  
  Extracted references between documents.

- `document_sources`  
  Source provenance for each version.

- `raw_artifacts`  
  Metadata for raw files and their storage paths.

- `ingestion_jobs`  
  Queue and operational trail for Stage 1 ingestion.

## 3. Stage 2 Retrieval Tables

- `retrieval_chunks`  
  Search-oriented chunks derived from `document_nodes`.

- `chunk_embeddings`  
  Dense embeddings for deduplicated retrieval chunk hashes.

The core relationship is:

`document_versions` -> `document_nodes` -> `retrieval_chunks` -> `chunk_embeddings`

## 4. Session and Answer Runtime

- `qa_sessions`
- `qa_queries`
- `qa_subtasks`
- `qa_messages`
- `qa_answers`
- `qa_evidence`

These tables represent:

- user session state;
- incoming queries;
- decomposition into subtasks;
- persisted conversation history;
- final answers;
- evidence used to build answers.

## 5. Freshness, Audit, and Tools

- `freshness_checks`
- `audit_events`
- `tool_invocations`
- `search_events`
- `verification_reports`

These tables cover:

- freshness decisions;
- audit trail;
- tool execution tracing;
- provenance;
- verification outputs.

