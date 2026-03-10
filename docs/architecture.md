# Архитектура Stage 1

QANorm сейчас представляет собой локальный ingestion-пайплайн нормативных документов.

Основные подсистемы:

- `crawler/` для обхода seed-страниц и пагинации;
- `parsers/` для list pages, document cards, HTML и PDF;
- `fetchers/` для HTTP/HTML/PDF/images загрузки;
- `ocr/` для OCR fallback;
- `normalizers/` для статусов, кодов, структуры и локаторов;
- `storage/` для raw-file storage;
- `jobs/` для очереди и worker;
- `services/` для orchestration ingestion-процессов;
- `indexing/` для node-level FTS/vector индексации;
- `repositories/` и `models/` для доступа к данным.

Поток обработки:

1. `crawl-seeds`
2. `parse_list_page`
3. `process_document_card`
4. `download_artifacts`
5. `extract_text`
6. `run_ocr` при необходимости
7. `normalize_document`
8. `index_document`

Worker выполняет шаги через очередь `ingestion_jobs` в PostgreSQL.
