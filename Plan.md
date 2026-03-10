# План реализации

## Назначение

Документ фиксирует целевое состояние репозитория как локальной нормативной базы Stage 1.

## Границы проекта

В репозитории остаются только компоненты локальной нормативной базы:

- crawler seed-страниц и пагинации;
- parser list/card/document;
- raw storage;
- извлечение текста и OCR fallback;
- нормализация структуры документа;
- модели данных Stage 1;
- очередь ingestion-задач и worker;
- node-level индексация и переиндексация;
- метрики качества наполнения базы.

В проект не входят:

- агентный runtime;
- консультативный retrieval/runtime слой;
- пользовательские интерфейсы консультативного слоя;
- trusted/open web search;
- вспомогательная оркестрация консультативного слоя;
- runtime-specific audit, verification и UI-oriented observability.

## Целевой результат

После завершения Stage 1 система должна:

1. Собирать документы из утвержденных seed-разделов.
2. Отбирать только релевантные документы по нормализованному статусу.
3. Хранить документы, версии, источники и raw-артефакты в локальной БД и файловом хранилище.
4. Извлекать текст из HTML/PDF и использовать OCR как fallback.
5. Нормализовать документ до иерархии `document_nodes`.
6. Поддерживать повторный запуск, дедупликацию и обновление версий.
7. Строить и поддерживать node-level индексацию для локального поиска и контроля полноты базы.

## Рабочие блоки

### 1. Конфигурация и инфраструктура

- поддержка `.env`, `configs/app.yaml`, `configs/sources.yaml`, `configs/statuses.yaml`, `configs/logging.yaml`;
- локальная работа через Python CLI без дополнительных runtime-сервисов, кроме PostgreSQL.

### 2. Схема данных

- `documents`
- `document_versions`
- `document_sources`
- `raw_artifacts`
- `document_nodes`
- `document_references`
- `ingestion_jobs`
- `update_events`

### 3. Ingestion pipeline

- crawl seed pages;
- parse list pages;
- process document cards;
- download artifacts;
- extract text;
- run OCR;
- normalize document;
- index document;
- refresh outdated documents.

### 4. Контроль качества

- ingestion metrics;
- Stage 1 readiness checklist;
- unit и integration tests для crawler/parser/storage/indexing/worker.

## Правило для следующих этапов

Любой новый консультативный, retrieval или агентный слой должен проектироваться как отдельный новый этап поверх Stage 1 базы.
