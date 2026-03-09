# QANorm Configuration

## 1. Configuration Sources

The project uses two configuration layers:

- `.env`  
  Secrets, service URLs, provider credentials, and runtime endpoints.

- `configs/*.yaml`  
  Application settings such as timeouts, provider selection, QA runtime behavior, trusted sources, and policy values.

## 2. Environment Variables

The common prefix is:

```text
QANORM_
```

Environment variables are loaded through `EnvironmentSettings` from:

- `.env`

The template file is:

- `.env.example`

## 3. Key Environment Variables

### Core Runtime

- `QANORM_APP_ENV`
- `QANORM_DB_URL`
- `QANORM_RAW_STORAGE_PATH`
- `QANORM_LOG_LEVEL`
- `QANORM_REDIS_URL`
- `QANORM_API_PUBLIC_URL`
- `QANORM_WEB_PUBLIC_URL`
- `QANORM_SEARXNG_BASE_URL`

### Remote Model Providers

- `QANORM_GEMINI_API_KEY`
- `QANORM_OPENAI_API_KEY`
- `QANORM_ANTHROPIC_API_KEY`
- `QANORM_QWEN_API_KEY`
- `QANORM_DEEPSEEK_API_KEY`

### Local Model Providers

- `QANORM_OLLAMA_BASE_URL`
- `QANORM_LMSTUDIO_BASE_URL`
- `QANORM_VLLM_BASE_URL`

### Telegram

- `QANORM_TELEGRAM_BOT_TOKEN`

## 4. `configs/app.yaml`

This file stores shared application runtime settings.

```yaml
app:
  request_timeout_seconds: 30
  max_retries: 3
  rate_limit_per_second: 2.0
  user_agent: "QANormBot/0.1"
  ocr_render_dpi: 300
  ocr_low_confidence_threshold: 0.7
```

Meaning:

- `request_timeout_seconds`  
  Base timeout for provider requests.

- `max_retries`  
  Retry budget for provider calls.

- `rate_limit_per_second`  
  Application-level rate limit.

## 5. `configs/qa.yaml`

This file defines Stage 2 runtime behavior.

Key sections:

- `qa.session`
- `qa.providers`
- `qa.web`
- `qa.telegram`
- `qa.search`

### Provider Selection

The runtime separates three provider roles:

- orchestration provider;
- synthesis provider;
- embeddings provider.

Embedding dimensionality is configured independently through `embedding_output_dimensions`.

### Web

- `stream_transport`
- `session_cookie_name`

### Telegram

- `enabled`
- `use_webhook`
- `max_message_length`
- `long_polling_timeout_seconds`
- `parse_mode`

### Search

- `open_web_provider`
- `open_web_max_results`
- `trusted_domains`

## 6. `configs/trusted_sources.yaml`

This file defines allowlisted trusted-source adapters.

Each source may specify:

- domain;
- sitemap URLs;
- seed URLs;
- allowed prefixes;
- synchronization limits;
- trusted-source chunk size and overlap.

## 7. Web UI Configuration

The frontend resolves the API base URL using:

- `QANORM_PUBLIC_API_BASE_URL`
- fallback to `NEXT_PUBLIC_QANORM_API_BASE_URL`
- fallback to `http://localhost:8000`

This keeps the frontend deployable independently from the API endpoint.

## 8. Docker Compose Configuration

`docker-compose.stage2.yml` injects into containers:

- database URL;
- Redis URL;
- public URLs;
- provider keys;
- local provider base URLs.

`api` and `worker` use the same backend image with different startup commands.

## 9. Embedding Backfill Tuning

The embedding provider is selected through `configs/qa.yaml`, while the backfill runner accepts additional operational parameters:

- `--batch-size`
- `--checkpoint-every-batches`
- `--generation-batches-per-run`
- `--request-timeout-seconds`

This allows large-scale embedding runs to be tuned without changing the entire application runtime.

## 10. Important Distinctions

- `.env`  
  Used directly by the running application.

- `.env.example`  
  Template for repeatable environment setup and onboarding.

- `configs/*.yaml`  
  Versioned runtime configuration stored in the repository.

