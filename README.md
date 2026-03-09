# QANorm

## 1. What the Project Does

QANorm is a local regulatory knowledge base and engineering assistant for regulatory and engineering workflows.

It combines:

- a pipeline that collects and normalizes regulatory documents into a local PostgreSQL database;
- a chunk-based retrieval layer with dense embeddings;
- an orchestrator-first assistant runtime for web and Telegram;
- freshness checks, trusted-source retrieval, open-web fallback, verification, audit, and observability.

Core capabilities:

- local regulatory corpus with versioned documents;
- raw HTML/PDF artifact storage;
- text extraction with OCR fallback;
- structural normalization into `document_nodes`;
- chunk-based retrieval through `retrieval_chunks`;
- dense retrieval through `chunk_embeddings`;
- session-scoped assistant runtime;
- non-blocking freshness checks and document refresh;
- trusted-source and open-web fallback;
- audit trail and observability stack.

## 2. Supported Backends

QANorm is vendor-agnostic at the provider layer.

### Chat / orchestration / synthesis

- Gemini
- OpenAI
- Anthropic
- Qwen
- DeepSeek
- Ollama
- LM Studio
- vLLM

### Embeddings

- Gemini
- OpenAI
- Ollama
- LM Studio
- vLLM

### Search

- self-hosted SearXNG for open-web search;
- allowlisted trusted-source adapters for controlled external sources.

Any concrete model exposed by a supported backend can be configured through `configs/qa.yaml`, as long as it provides the required capability.

## 3. Quick Local Setup

### Prerequisites

Required:

- Python `3.12`
- Node.js `20`
- PostgreSQL `16` with `pgvector`
- Redis `7`

Recommended:

- Docker Desktop
- Tesseract OCR for full document extraction with OCR fallback

### Clone the repository

```powershell
git clone <your-repo-url> QANorm
cd QANorm
```

### Create the Python environment

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
```

### Install frontend dependencies

```powershell
cd web
npm install
cd ..
```

### Create the environment file

```powershell
Copy-Item .env.example .env
```

At minimum, review and fill:

- `QANORM_DB_URL`
- `QANORM_REDIS_URL`
- `QANORM_API_PUBLIC_URL`
- `QANORM_WEB_PUBLIC_URL`
- `QANORM_GEMINI_API_KEY` if Gemini embeddings are enabled

### Start PostgreSQL 16 + pgvector

If you do not already have Postgres running locally:

```powershell
docker run --name qanorm-pg16 `
  -e POSTGRES_DB=qanorm `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  -d pgvector/pgvector:pg16
```

Enable the extension:

```powershell
docker exec qanorm-pg16 psql -U postgres -d qanorm -c "create extension if not exists vector;"
```

### Start Redis

```powershell
docker run --name qanorm-redis -p 6379:6379 -d redis:7-alpine
```

### Apply migrations

```powershell
qanorm init-db
```

Equivalent:

```powershell
alembic upgrade head
```

## 4. Prepare the Database

If the database is empty, populate the local regulatory corpus first.

### Validate configuration

```powershell
qanorm check-config
```

### Run the seed crawl

```powershell
qanorm crawl-seeds
```

### Run the worker until the queue is drained

```powershell
qanorm run-worker
```

Repeat as needed until there are no pending ingestion jobs.

### Review ingestion quality

```powershell
qanorm ingestion-metrics
qanorm ingestion-report
```

At this point the local regulatory database is ready for assistant retrieval.

## 5. Run the Assistant

### Start the API

```powershell
.\\.venv\\Scripts\\python.exe -m uvicorn qanorm.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

### Start the worker

```powershell
.\\.venv\\Scripts\\python.exe -m qanorm.workers.bootstrap
```

### Start the web UI

```powershell
cd web
npm run dev
```

Open:

```text
http://localhost:3000
```

### Optional: run through Docker Compose

If PostgreSQL is already available on `localhost:5432`, you can start the Stage 2 runtime with Docker Compose:

```powershell
docker compose -f docker-compose.stage2.yml --profile core up --build -d
```

Optional profiles:

```powershell
docker compose -f docker-compose.stage2.yml --profile search up -d
docker compose -f docker-compose.stage2.yml --profile obs up -d
```

### Health checks

```powershell
Invoke-WebRequest http://localhost:8000/health/live
Invoke-WebRequest http://localhost:8000/health/ready
Invoke-WebRequest http://localhost:8000/metrics
```

### Optional: Telegram

Set:

- `QANORM_TELEGRAM_BOT_TOKEN`

And enable Telegram in `configs/qa.yaml`.

## 6. Documentation Links

Start with:

- [docs/README.md](docs/README.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/retrieval.md](docs/retrieval.md)
- [docs/operations.md](docs/operations.md)
- [docs/configuration.md](docs/configuration.md)

Additional useful files:

- [docs/data-model.md](docs/data-model.md)
- [docs/agents.md](docs/agents.md)
- [docs/api.md](docs/api.md)
- [docs/security.md](docs/security.md)
- [docs/testing.md](docs/testing.md)

