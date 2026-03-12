# QANorm

QANorm сейчас находится в состоянии `Stage 2A MVP`:

- `Stage 1` хранит локальную нормативную базу и ingestion pipeline;
- `Stage 2A` строит agentic RAG поверх локальной базы;
- интерфейс MVP работает через `Streamlit`;
- dense retrieval использует `retrieval_units` и `pgvector`.

Основные документы проекта:

- [SPECIFICATION.md](SPECIFICATION.md)
- [Plan.md](Plan.md)
- [Tasks.md](Tasks.md)

## Что есть в репозитории

- ingestion и нормализация документов до `document_nodes`;
- derived retrieval layer: `document_aliases` и `retrieval_units`;
- backfill embeddings для `retrieval_units`;
- hybrid retrieval engine: explicit document, locator, lexical, dense, rerank;
- DSPy-based `ControllerAgent`, `Composer`, `GroundingVerifier`;
- `Streamlit` MVP UI.

## Требования

- Windows PowerShell
- Python `3.12`
- Docker Desktop
- PostgreSQL `16` с расширением `pgvector`
- Tesseract OCR в системе, если нужен OCR fallback
- рабочий Gemini API key

## Быстрый запуск с нуля

### 1. Поднять PostgreSQL в Docker

Если контейнера еще нет:

```powershell
docker run -d `
  --name qanorm-pg16 `
  -e POSTGRES_DB=qanorm `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  pgvector/pgvector:pg16
```

Если контейнер уже создан, но остановлен:

```powershell
docker start qanorm-pg16
```

Проверить, что контейнер поднялся:

```powershell
docker ps
```

### 2. Создать и заполнить виртуальное окружение

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
```

### 3. Создать `.env`

Скопируйте шаблон:

```powershell
Copy-Item .env.example .env
```

Минимально заполните в `.env`:

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

Назначение переменных:

- `QANORM_DB_URL` — строка подключения к PostgreSQL
- `QANORM_RAW_STORAGE_PATH` — каталог raw-файлов и служебных stage2a state/log
- `QANORM_STAGE2A_CONFIG_PATH` — путь к `configs/stage2a.yaml`
- `QANORM_GEMINI_API_BASE_URL` — Gemini REST base URL
- `QANORM_GEMINI_API_KEY` — ключ Gemini
- `QANORM_DSPY_CACHE_DIR` — каталог DSPy cache

### 4. Применить миграции

```powershell
qanorm init-db
```

Проверка:

```powershell
qanorm health-check
qanorm check-config
```

## Типовой порядок запуска проекта

Ниже два сценария:

- первый запуск с пустой БД;
- повторный запуск, когда база уже наполнена.

### Сценарий A. Первый запуск с пустой БД

#### Шаг 1. Загрузить Stage 1 corpus

Запустить seed crawl:

```powershell
qanorm crawl-seeds
```

Запустить worker ingestion:

```powershell
qanorm run-worker
```

Если нужен точечный refresh:

```powershell
qanorm refresh-document "СП 63.13330.2018"
qanorm update-document "СП 63.13330.2018"
```

Посмотреть метрики ingestion:

```powershell
qanorm ingestion-metrics
qanorm ingestion-report
```

#### Шаг 2. Построить derived retrieval data

Полная пересборка:

```powershell
qanorm stage2a-rebuild-derived
```

Или в фоне:

```powershell
qanorm stage2a-derived-start --parallel-workers 4
qanorm stage2a-derived-status
```

Отдельные команды:

```powershell
qanorm stage2a-build-aliases
qanorm stage2a-build-units
```

#### Шаг 3. Оценить объем embeddings

```powershell
qanorm stage2a-embed-preflight
```

#### Шаг 4. Сгенерировать embeddings для `retrieval_units`

Простой запуск:

```powershell
qanorm stage2a-embed-start
```

Фоновый параллельный запуск:

```powershell
qanorm stage2a-embed-start --parallel-workers 2
qanorm stage2a-embed-status
```

Если backfill уже шел раньше, та же команда продолжит его по state-файлам.

### Сценарий B. Повторный запуск, когда база уже готова

Если Stage 1 corpus, derived data и embeddings уже построены, обычно достаточно:

```powershell
docker start qanorm-pg16
.\.venv\Scripts\Activate.ps1
qanorm check-config
```

Если нужно убедиться, что embeddings полностью готовы:

```powershell
qanorm stage2a-embed-status
```

## Запуск Stage 2A UI

Запускать из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/qanorm/stage2a/ui/app.py --server.address 127.0.0.1 --server.port 8501
```

После запуска открыть:

```text
http://127.0.0.1:8501
```

Что должно работать в UI:

- ввод инженерного нормативного вопроса;
- ответ с evidence;
- citations и limitations;
- debug trace шагов `ReAct-lite`.

## Полезные CLI-команды

### Stage 1

```powershell
qanorm check-config
qanorm health-check
qanorm crawl-seeds
qanorm run-worker
qanorm reindex
qanorm ingestion-metrics
qanorm ingestion-report
qanorm refresh-document "СП 20.13330.2016"
qanorm update-document "СП 20.13330.2016"
```

### Stage 2A derived data

```powershell
qanorm stage2a-build-aliases
qanorm stage2a-build-units
qanorm stage2a-rebuild-derived
qanorm stage2a-derived-start --parallel-workers 4
qanorm stage2a-derived-status
```

### Stage 2A embeddings

```powershell
qanorm stage2a-embed-preflight
qanorm stage2a-embed-start --parallel-workers 2
qanorm stage2a-embed-status
```

## Где лежат служебные state и log файлы

Stage 2A backfill пишет state и log рядом с `QANORM_RAW_STORAGE_PATH`, в каталог:

```text
<parent of QANORM_RAW_STORAGE_PATH>\stage2a\
```

Там появляются:

- `derived_backfill_state*.json`
- `derived_backfill*.log`
- `embedding_backfill_state*.json`
- `embedding_backfill*.log`
- `*_manifest.json`

## Как понять, что система готова к работе

Минимальный чек-лист:

1. `docker ps` показывает `qanorm-pg16`.
2. `qanorm check-config` проходит без ошибок.
3. `qanorm init-db` уже применен.
4. В БД есть документы и `retrieval_units`.
5. `qanorm stage2a-embed-status` показывает, что `pending` нет.
6. `streamlit run ...` поднимает UI.

## Тесты

Полный прогон:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q
```

Только retrieval:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_stage2a_retrieval_engine.py tests/integration/test_stage2a_retrieval_integration.py -q
```

## Остановка

Остановить UI:

- `Ctrl+C` в окне `streamlit`

Остановить PostgreSQL контейнер:

```powershell
docker stop qanorm-pg16
```

Остановить все контейнеры:

```powershell
docker stop $(docker ps -q)
```

На PowerShell, если нужна совместимая форма:

```powershell
$ids = docker ps -q
if ($ids) { docker stop $ids }
```

## Текущее состояние разработки

На данный момент:

- `Stage 1` сохранен и рабочий;
- `Stage 2A` реализован как `DSPy-hybrid` MVP;
- dense embeddings строятся и используются на уровне `retrieval_units`, а не `document_nodes`;
- запуск проекта и операционные команды описаны в этом README;
- детальный план и прогресс ведутся в [Tasks.md](Tasks.md).
