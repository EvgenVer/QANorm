# Конфигурация Stage 1

Используются:

- `.env`
- `configs/app.yaml`
- `configs/sources.yaml`
- `configs/statuses.yaml`
- `configs/logging.yaml`

Ключевые переменные окружения:

- `QANORM_DB_URL`
- `QANORM_RAW_STORAGE_PATH`
- `QANORM_APP_ENV`
- `QANORM_LOG_LEVEL`

Основные YAML-файлы:

- `app.yaml` управляет timeout/retry/rate-limit и OCR порогами;
- `sources.yaml` задает seed URL;
- `statuses.yaml` задает правила нормализации статусов;
- `logging.yaml` задает конфигурацию логирования.
