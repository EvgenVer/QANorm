# QANorm Architecture

## 1. System Overview

QANorm is split into two logical layers:

- `Stage 1`  
  Builds the local regulatory corpus: crawling, raw artifact storage, text extraction, normalization, structural document representation, and versioning.

- `Stage 2`  
  Builds the engineering assistant runtime: retrieval, orchestration, answer synthesis, freshness checks, web and Telegram access, observability, and audit.

The project is implemented as a modular monolith. This keeps orchestration, retrieval, audit, and provider abstraction inside one controlled runtime boundary.

## 2. Stage 1

Stage 1 produces the canonical local regulatory knowledge base.

Main steps:

1. Seed crawling and ingestion job creation.
2. Raw HTML/PDF and artifact download.
3. Text extraction with OCR fallback.
4. Document normalization into `document_nodes`.
5. Cross-document reference extraction.
6. Version management through `documents` and `document_versions`.

Main Stage 1 outputs:

- local regulatory corpus;
- raw storage;
- structural document layer;
- reference graph;
- operational ingestion trail.

## 3. Stage 2

Stage 2 builds the engineering assistant on top of the local corpus.

Key design principles:

- `orchestrator-first` architecture;
- strongly typed runtime state;
- provider abstraction for LLMs and embeddings;
- tool layer with policy checks;
- session-scoped memory;
- async multi-session execution;
- non-blocking freshness checks;
- audit trail and observability.

## 4. Main Stage 2 Components

### Backend API

`FastAPI` provides the HTTP API for:

- sessions;
- queries and answers;
- SSE progress streaming;
- health and metrics endpoints.

Key route modules:

- `src/qanorm/api/routes/sessions.py`
- `src/qanorm/api/routes/chat.py`
- `src/qanorm/api/routes/health.py`
- `src/qanorm/api/routes/metrics.py`

### Worker Runtime

Background jobs run through `ARQ` and `Redis`.

The worker is responsible for:

- orchestration jobs;
- session cleanup;
- freshness checks;
- document refresh;
- trusted source synchronization;
- other background operations required by Stage 2.

### Retrieval Layer

Retrieval is built on a dedicated search layer:

- `retrieval_chunks`
- `chunk_embeddings`

The data path is:

`document_nodes` -> chunking -> `retrieval_chunks` -> embeddings -> exact / FTS / vector / hybrid retrieval

This separation allows the system to:

- keep `document_nodes` as the canonical structural layer;
- reduce dense storage size;
- lower embedding generation cost;
- reconstruct precise locators and quotes from the structural layer.

### Orchestrator and Agent Roles

The main answer runtime is coordinated by one orchestrator.

It manages:

- query analysis;
- task decomposition;
- normative retrieval;
- answer synthesis;
- verification;
- freshness;
- trusted and open-web fallback.

Sub-agents are implemented as specialized roles inside the same runtime rather than as separate network services.

### Web UI

The frontend lives in `web/` and is implemented with `Next.js`.

The web channel provides:

- chat interface;
- session list;
- query submission;
- SSE stream handling;
- rendering of answers, evidence, warnings, and limitations.

### Telegram

The Telegram adapter lives in:

- `src/qanorm/integrations/telegram/bot.py`

It uses the same application core as the web UI: query/session services, answer persistence, and audit trail are shared.

### Observability and Audit

The runtime includes:

- structured JSON logging;
- correlation IDs;
- metrics export;
- tracing hooks;
- audit writer;
- local observability stack through `Prometheus`, `Grafana`, `Loki`, and `Tempo`.

## 5. Stage 2 Data Flow

A normative question flows through the system as follows:

1. A user sends a message from the web UI or Telegram.
2. A `qa_query` and a user message are created.
3. The orchestrator builds a plan and subtasks.
4. Retrieval searches `retrieval_chunks` for evidence.
5. Trusted sources and open web are used when required.
6. The answer synthesizer assembles a structured answer.
7. The verification layer checks coverage, citations, and supportedness.
8. Freshness checks and document refresh may run in parallel.
9. Final answer and evidence are persisted.
10. The delivery channel receives the answer, warnings, and citations.

## 6. Why Retrieval Is Separate from `document_nodes`

`document_nodes` solve structural normalization, but they are not an efficient dense retrieval unit.

Moving retrieval into `retrieval_chunks` solves several problems:

- fewer embedding objects;
- no dense storage for purely structural helper nodes;
- cheaper large-scale re-embedding;
- clean separation between canonical document structure and search-oriented data.

## 7. Local Runtime Profiles

`docker-compose.stage2.yml` is split into profiles:

- `core`  
  `redis`, `api`, `worker`, `web`

- `search`  
  `searxng`

- `obs`  
  `prometheus`, `grafana`, `loki`, `tempo`

- `db`  
  isolated `postgres` for clean environment runs

The main development workflow uses the populated Stage 1 database as the primary regulatory store.

