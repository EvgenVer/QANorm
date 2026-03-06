# QANorm

QANorm is a staged project for building:

- a local normalized corpus of regulatory documents;
- an ingestion pipeline for collecting and indexing those documents;
- an evidence-based engineering assistant on top of that corpus.

## Stages

### Stage 1

Stage 1 builds the regulatory knowledge base:

- crawling and ingestion from approved seeds;
- raw artifact storage;
- text extraction and OCR fallback;
- normalization into structured nodes;
- full-text and vector indexing;
- document versioning and refresh.

### Stage 2

Stage 2 builds the assistant runtime:

- a primary orchestrator with specialized agent modules;
- session-scoped memory for web and Telegram channels;
- evidence-based retrieval over the Stage 1 database;
- non-blocking freshness checks and document refresh;
- trusted-source and open-web fallback;
- verification, security guards, observability and audit trail.

## Architecture Summary

The repository is organized as a modular monolith.

- `src/qanorm/` contains the Python application code.
- `configs/` contains YAML runtime configuration.
- `data/` stores local raw and derived artifacts for Stage 1.
- `web/` contains the Stage 2 web client scaffold.

Stage 2 is designed around one orchestrator, typed state, provider adapters,
tool policies and a bounded verification loop. External model vendors are kept
behind provider interfaces so the business logic does not depend on one API.
