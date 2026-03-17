# QANorm

QANorm is a local normative QA system for Russian construction standards.

Current state:
- `Stage 1`: corpus ingestion, normalization, and storage in PostgreSQL
- `Stage 2A`: agentic retrieval and grounded answers over the local corpus
- `Stage 2B`: conversational memory inside one browser session, local multi-session chat UI, and streamed debug trace

Core project documents:
- `SPECIFICATION.md`
- `Plan.md`
- `Tasks.md`

## Stack

- Python `3.12`
- PostgreSQL `16` + `pgvector`
- Streamlit UI
- DSPy only in the agent layer
- Custom retrieval over `document_aliases`, `document_nodes`, and `retrieval_units`
- Gemini API for controller/composer/verifier/embeddings

## What is implemented

- Stage 1 ingestion pipeline and normalized local corpus
- Derived retrieval data:
  - `document_aliases`
  - `retrieval_units`
- Dense retrieval with embeddings in PostgreSQL
- DSPy-based `ControllerAgent`, `Composer`, `GroundingVerifier`
- Conversational UI with:
  - local session memory
  - multiple local chat sessions in one browser session
  - new/reset session controls
  - streamed runtime/debug events in chat

## Current quality snapshot

Latest detached eval run on the full `150` question set:
- `document hit@3 = 0.86`
- `locator hit@5 = 1.00`
- `grounded answer rate = 0.98`
- `unsupported claim rate = 0.00`
- `partial answer rate = 0.0867`
- `expected mode match rate = 0.72`
- `wrong document rate = 0.00`

This means the system is usable for manual testing, but there are still known quality gaps in some clusters such as `GOST 27751`, fire-safety families, and a subset of reinforced-concrete follow-up cases.

## Requirements

- Windows PowerShell
- Python `3.12`
- Docker Desktop
- Gemini API key

Optional:
- Tesseract OCR if OCR fallback is needed for ingestion

## Setup

### 1. Create and populate the virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
```

### 2. Create `.env`

```powershell
Copy-Item .env.example .env
```

Minimum required values:

```dotenv
QANORM_APP_ENV=local
QANORM_DB_URL=postgresql+psycopg://postgres:postgres@localhost:5432/qanorm
QANORM_RAW_STORAGE_PATH=./data/raw
QANORM_LOG_LEVEL=INFO

QANORM_STAGE2A_CONFIG_PATH=configs/stage2a.yaml
QANORM_GEMINI_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta
QANORM_DSPY_CACHE_DIR=.cache/dspy
QANORM_GEMINI_API_KEY=YOUR_REAL_KEY
```

### 3. Start PostgreSQL

If the container does not exist yet:

```powershell
docker run -d `
  --name qanorm-pg16 `
  -e POSTGRES_DB=qanorm `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  pgvector/pgvector:pg16
```

If the container already exists:

```powershell
docker start qanorm-pg16
```

### 4. Apply migrations

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main init-db
```

### 5. Check configuration

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main check-config
.\.venv\Scripts\python.exe -m qanorm.cli.main health-check
```

## Data preparation

### Stage 1 ingestion

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main crawl-seeds
.\.venv\Scripts\python.exe -m qanorm.cli.main run-worker
```

Useful commands:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main refresh-document "SP 63.13330.2018"
.\.venv\Scripts\python.exe -m qanorm.cli.main update-document "SP 63.13330.2018"
.\.venv\Scripts\python.exe -m qanorm.cli.main ingestion-metrics
.\.venv\Scripts\python.exe -m qanorm.cli.main ingestion-report
```

### Stage 2A derived retrieval data

One-shot rebuild:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-rebuild-derived
```

Detached parallel rebuild:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-derived-start --parallel-workers 4
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-derived-status
```

### Stage 2A embeddings

Preflight:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-embed-preflight
```

Detached embedding backfill:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-embed-start --parallel-workers 2
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-embed-status
```

## Run the UI

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/qanorm/stage2a/ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Open:

```text
http://127.0.0.1:8501
```

Notes:
- the current UI title comes from `configs/stage2a.yaml` and may still say `QANorm Stage 2A`
- chat sessions are local to the current browser session only
- reloading the page starts from a clean local state

## Stage 2B behavior

Implemented conversational features:
- follow-up and clarification turns reuse local session memory
- users can create a new local session from the sidebar
- users can reset only the active session
- runtime/debug events are streamed during answer generation
- evidence, limitations, and debug panels are collapsed by default after the answer

Not implemented in Stage 2B:
- login/auth
- persistent chat history in PostgreSQL
- restoring sessions after page reload

## Eval commands

Single process:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-eval --questions-path eval/stage2a/questions.jsonl
```

Detached parallel eval:

```powershell
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-eval-start --questions-path eval/stage2a/questions.jsonl --parallel-workers 4 --manifest-path .cache/stage2a_eval/eval_manifest.json
.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-eval-status --manifest-path .cache/stage2a_eval/eval_manifest.json
```

## Manual testing

Manual smoke checklist for Stage 2B:
- `docs/stage2b-streamlit-smoke.md`

Readiness reports:
- `docs/stage2a-mvp-readiness-20260316.md`
- `docs/stage2b-mvp-readiness-20260317.md`

## Shutdown

Stop UI:

```powershell
Get-Process | Where-Object { $_.ProcessName -eq 'python' -and $_.Path -like '*QANorm*' } | Stop-Process -Force
```

Stop PostgreSQL container:

```powershell
docker stop qanorm-pg16
```
