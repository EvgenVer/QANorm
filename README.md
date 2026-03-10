# QANorm

QANorm сейчас зафиксирован как проект Stage 1: локальная база нормативных документов и ingestion-конвейер.

В репозитории оставлены только компоненты, необходимые для:

- обхода seed-разделов `meganorm.ru`;
- загрузки карточек и raw-артефактов документов;
- извлечения текста из HTML/PDF и OCR fallback;
- нормализации структуры документа до `document_nodes`;
- хранения документов, версий, источников, raw-файлов и очереди ingestion-задач;
- node-level индексации для локального поиска и последующих экспериментов.

## Локальный запуск

Требуется:

- Python `3.12`
- PostgreSQL `16` с `pgvector`
- Tesseract OCR для OCR fallback

Подготовка:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .[dev]
Copy-Item .env.example .env
```

Минимально заполните в `.env`:

- `QANORM_DB_URL`
- `QANORM_RAW_STORAGE_PATH` при необходимости

Инициализация БД:

```powershell
qanorm init-db
```

Базовые команды:

```powershell
qanorm check-config
qanorm crawl-seeds
qanorm run-worker
qanorm reindex
qanorm ingestion-metrics
qanorm ingestion-report
```

## Документация

- [docs/README.md](docs/README.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/data-model.md](docs/data-model.md)
- [docs/operations.md](docs/operations.md)
- [docs/testing.md](docs/testing.md)
