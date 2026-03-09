# Operations and Local Runtime

## 1. Main Operational Scenarios

This document covers the practical runtime commands for QANorm:

- running API, worker, and web locally;
- running through Docker Compose;
- checking health endpoints;
- monitoring embedding backfill;
- reading logs and metrics;
- pausing the runtime safely.

## 2. Local Run Without Docker

### Backend API

```powershell
.\\.venv\\Scripts\\python.exe -m uvicorn qanorm.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

### Worker

```powershell
.\\.venv\\Scripts\\python.exe -m qanorm.workers.bootstrap
```

### Web UI

```powershell
cd web
npm run dev
```

### Telegram Bot

Telegram startup depends on:

- `QANORM_TELEGRAM_BOT_TOKEN`
- `qa.telegram.enabled`

Once enabled, the bot uses the same query/session runtime as the web channel.

## 3. Docker Compose Runtime

Base local stack:

```powershell
docker compose -f docker-compose.stage2.yml --profile core up --build -d
```

Additional profiles:

```powershell
docker compose -f docker-compose.stage2.yml --profile search up -d
docker compose -f docker-compose.stage2.yml --profile obs up -d
docker compose -f docker-compose.stage2.yml --profile db up -d
```

Notes:

- `core` uses the main Stage 1 database through `host.docker.internal:5432`
- `db` starts an isolated Postgres instance for clean environment runs

## 4. Health Checks

### API Liveness

```powershell
Invoke-WebRequest http://localhost:8000/health/live
```

### API Readiness

```powershell
Invoke-WebRequest http://localhost:8000/health/ready
```

### Metrics

```powershell
Invoke-WebRequest http://localhost:8000/metrics
```

### Web UI

```text
http://localhost:3000
```

## 5. Docker Container Checks

```powershell
docker ps
```

For the Stage 2 compose stack:

```powershell
docker compose -f docker-compose.stage2.yml ps
```

## 6. Embedding Backfill Monitoring

### Check Running Backfill Processes

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*backfill_chunk_embeddings.py*' } | Select-Object ProcessId,CommandLine
```

### Follow Backfill Logs

```powershell
Get-Content -Wait data\\logs\\chunk_embeddings_backfill_fast.log
```

### Check Progress in the Database

```powershell
docker exec qanorm-pg16 psql -U postgres -d qanorm -c "select count(*) as saved, 174716 - count(*) as remaining from chunk_embeddings;"
```

### Stop Backfill

```powershell
Stop-Process -Id <PID> -Force
```

The workflow is resumable, so it can be restarted safely after an interruption.

## 7. Logs

Local process and background-run logs are stored in:

- `data/logs/`

Typical files:

- `chunk_embeddings_backfill_fast.log`
- `chunk_embeddings_backfill_fast.err.log`
- ingestion and worker logs

## 8. Metrics and Observability

The runtime exposes:

- JSON logging;
- correlation IDs;
- Prometheus-compatible `/metrics`;
- tracing hooks;
- observability stack via `Prometheus`, `Grafana`, `Loki`, and `Tempo`.

If the `obs` profile is running:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`

## 9. Common Problems

### Docker Daemon Is Unavailable

Symptoms:

- `docker ps` fails;
- containers cannot be inspected.

Action:

- start Docker Desktop;
- re-run `docker ps`.

### API Does Not Start Because of Configuration

Check:

- `.env`
- `configs/app.yaml`
- `configs/qa.yaml`

Especially:

- `QANORM_DB_URL`
- `QANORM_REDIS_URL`
- `QANORM_GEMINI_API_KEY`

### Backfill Is Slow or Appears Stuck

Check:

- whether the process is still alive;
- whether `chunk_embeddings` count is increasing;
- whether errors appear in `*.err.log`;
- whether requests are hitting timeouts or provider rate limits.

### UI Cannot Reach API

Check:

- `QANORM_PUBLIC_API_BASE_URL`
- `QANORM_API_PUBLIC_URL`
- `http://localhost:8000/health/live`

## 10. Safe Pause Procedure

To pause the local runtime safely:

1. Stop long-running background processes.
2. Ensure there are no hanging `idle in transaction` sessions.
3. Stop containers if needed:

```powershell
docker stop qanorm-pg16
docker compose -f docker-compose.stage2.yml down
```

Resumable workflows such as embedding backfill can be resumed later.

