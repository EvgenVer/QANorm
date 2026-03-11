# План реализации

## 1. Назначение

Документ фиксирует практический план реализации `Stage 2A` как быстрого `MVP` поверх уже существующей локальной нормативной базы `Stage 1`.

Главная цель:

- не перестраивать проект заново;
- не усложнять первую итерацию лишней инфраструктурой;
- быстро получить рабочую систему, которая отвечает по локальному нормативному корпусу и умеет показывать evidence.

---

## 2. Базовые ограничения MVP

Для первой итерации принимаются следующие ограничения:

- только локальная нормативная база `Stage 1`;
- `DSPy-hybrid`: кастомный retrieval/data layer и DSPy только для `ControllerAgent`, `Composer`, `GroundingVerifier`;
- `Gemini` как первый провайдер через `API key` и DSPy/provider config;
- только локальный интерфейс `Streamlit`;
- без отдельного backend API;
- без open web, trusted web и Telegram;
- без многопользовательского runtime;
- без тяжелой перестройки `Stage 1`.

---

## 3. Что требуется скорректировать в Stage 1

`Stage 1` уже является рабочей базой данных и ingestion-слоем. Его нужно не переписывать, а слегка подготовить под `Stage 2A`.

Обязательные доработки:

- добавить таблицу `document_aliases`;
- добавить в `document_nodes` поля:
  - `locator_raw`
  - `locator_normalized`
  - `heading_path`
- добавить индекс по `locator_normalized`;
- сохранить `document_nodes` как канонический structural layer;
- использовать `document_nodes` для `FTS`, locator lookup, neighbor expansion и citations;
- отказаться от dense embeddings на всех `document_nodes`;
- добавить derived слой `retrieval_units` для dense retrieval.

Что сознательно не делаем в MVP:

- не меняем радикально parser Stage 1;
- не переделываем базовую разбивку `document_nodes`;
- не строим сложный session/audit storage слой;
- не строим внешний retrieval database.

---

## 4. Принятые решения по бизнес-логике

Для `Stage 2A` фиксируются следующие правила:

- система отвечает только по локальному нормативному корпусу;
- инженерно-нормативные вопросы всегда идут в retrieval, даже если пользователь не указал норму явно;
- при отсутствии явного документа система обязана попытаться определить вероятные документы сама;
- если evidence недостаточно, система дает partial answer или shortlist вероятных документов, а не молчит;
- финальный ответ строится только по evidence pack;
- каждый существенный тезис ответа должен быть привязан к evidence;
- приоритет имеет прямой нормативный текст;
- в первой версии не допускаются ответы на основе web search или внешних справочных источников.

---

## 5. Принятая архитектура

### 5.1. Общая схема

Архитектура `Stage 2A`:

1. `Streamlit UI`
2. `ControllerAgent (DSPy ReAct-lite)`
3. `Retrieval Engine`
4. `Composer (DSPy module)`
5. `GroundingVerifier (DSPy module)`
6. `Stage 1 DB + retrieval_units`

### 5.2. Логика запроса

Путь запроса должен быть таким:

1. Пользователь задает вопрос в `Streamlit`.
2. `ControllerAgent` определяет следующую retrieval-операцию.
3. Retrieval engine:
   - определяет документ;
   - или подбирает shortlist документов;
   - или выполняет locator lookup;
   - или запускает hybrid retrieval.
4. Результаты объединяются и rerank-ятся.
5. Из лучших результатов собирается compact evidence pack.
6. `Composer` формирует ответ.
7. `GroundingVerifier` вырезает unsupported claims и при необходимости переводит ответ в partial mode.

### 5.3. ReAct-lite policy

В MVP используется один управляющий агент, реализованный на DSPy.

Ограничения цикла:

- не более `4-6` tool steps на запрос;
- не более `2` corrective итераций;
- без сложного multi-agent orchestration;
- без long-running workflow engine.

### 5.4. Retrieval model

Retrieval должен быть гибридным:

- `exact/code lookup`
- `document alias lookup`
- `locator lookup`
- `PostgreSQL FTS`
- `dense retrieval` по `retrieval_units`
- `rerank shortlist`

`document_nodes` остаются точным структурным слоем.

`retrieval_units` становятся основной semantic единицей dense retrieval.

---

## 6. Финальный стек

Принятый стек `Stage 2A`:

- `Python 3.12`
- `DSPy`
- `Streamlit`
- `httpx`
- `tenacity`
- `Pydantic v2`
- `pydantic-settings`
- `SQLAlchemy 2`
- `psycopg`
- `Alembic`
- `PostgreSQL 16`
- `PostgreSQL FTS`
- `pg_trgm`
- `pgvector`
- `pytest`
- `respx`

Что сознательно не берем в MVP:

- `LangChain`
- `LangGraph`
- `LlamaIndex`
- `FastAPI`
- `Redis`
- `Celery`
- `ARQ`
- отдельный vector DB

---

## 7. Структура проекта и модули

Ниже зафиксирована целевая минимальная структура `Stage 2A`. Она должна быть достаточно простой, чтобы быстро собрать MVP.

### 7.1. Сохраняемые Stage 1 модули

- `src/qanorm/crawlers`
  - обход seed-разделов и страниц списков.
- `src/qanorm/parsers`
  - парсинг карточек и документов.
- `src/qanorm/extractors`
  - извлечение текста из HTML/PDF.
- `src/qanorm/normalizers`
  - нормализация кодов, структуры и локаторов.
- `src/qanorm/indexing`
  - Stage 1 индексация и reindex.
- `src/qanorm/models`
  - ORM-модели Stage 1 и новые Stage 2A derived tables.
- `src/qanorm/repositories`
  - доступ к документам, версиям, узлам и derived retrieval данным.
- `src/qanorm/workers`
  - ingestion worker Stage 1.
- `src/qanorm/cli`
  - команды Stage 1 и команды подготовки derived retrieval data.

### 7.2. Новые модули Stage 2A

- `src/qanorm/stage2a/contracts`
  - Pydantic-схемы запросов, observations, evidence, answer models.
- `src/qanorm/stage2a/providers`
  - DSPy LM bootstrap, provider abstraction и Gemini-конфигурация.
- `src/qanorm/stage2a/retrieval`
  - parser, document discovery, resolver, lexical search, dense search, reranker, context builder.
- `src/qanorm/stage2a/agent`
  - DSPy-based `ControllerAgent` и `ReAct-lite` runtime.
- `src/qanorm/stage2a/composer`
  - DSPy-based `Composer` для grounded answer по evidence pack.
- `src/qanorm/stage2a/verifier`
  - DSPy-based `GroundingVerifier` для claim-to-evidence mapping и partial mode.
- `src/qanorm/stage2a/services`
  - прикладной orchestration слой для UI.
- `src/qanorm/stage2a/ui`
  - `Streamlit` приложение и presentation helpers.

### 7.3. Derived data modules

- `src/qanorm/stage2a/indexing`
  - построение `document_aliases` и `retrieval_units`.
- `src/qanorm/stage2a/repositories`
  - доступ к `document_aliases`, `retrieval_units` и evidence queries.

---

## 8. Укрупненный план реализации

Реализация должна идти короткими шагами, каждый из которых дает наблюдаемый прогресс.

### Шаг 1. Подготовить базу Stage 1 под Stage 2A

- добавить миграции для `document_aliases`, locator-полей и `retrieval_units`;
- добавить ORM-модели и репозитории;
- добавить индексы;
- подготовить backfill-команды.

Результат:

- база готова к document discovery, locator lookup и dense retrieval.

### Шаг 2. Подготовить derived retrieval data

- заполнить `document_aliases`;
- сформировать `document_card` units;
- сформировать `semantic_block` units;
- построить `FTS` и dense индексы для `retrieval_units`.

Результат:

- есть минимальный retrieval-ready слой поверх Stage 1.

### Шаг 3. Собрать retrieval engine

- реализовать query parsing;
- реализовать `resolve_document`;
- реализовать `discover_documents`;
- реализовать `lookup_locator`;
- реализовать lexical retrieval;
- реализовать dense retrieval;
- реализовать rerank shortlist;
- реализовать context builder.

Результат:

- retrieval engine возвращает компактный evidence pack.

### Шаг 4. Собрать agent runtime

- реализовать DSPy-based `ControllerAgent`;
- реализовать DSPy `ReAct-lite` loop;
- реализовать stop conditions;
- реализовать partial mode policy.

Результат:

- система умеет принимать вопрос и управлять retrieval шагами.

### Шаг 5. Собрать answer layer

- реализовать DSPy-based `Composer`;
- реализовать DSPy-based `GroundingVerifier`;
- реализовать answer/evidence DTO;
- реализовать потоковую выдачу ответа.

Результат:

- система умеет строить grounded answer и отсекать unsupported claims.

### Шаг 6. Собрать Streamlit MVP

- сделать chat-first интерфейс;
- вывести evidence panel;
- вывести citations;
- сделать debug view шагов runtime;
- подключить сервисный слой.

Результат:

- есть локальный рабочий UI для ручной приемки.

### Шаг 7. Добить тесты и приемку

- unit tests на parser, discovery, resolver, retrieval, composer, verifier;
- integration tests на end-to-end happy path;
- smoke сценарии в Streamlit;
- первый локальный eval-набор.

Результат:

- есть минимальное подтверждение качества MVP.

---

## 9. Критерии приемки

`Stage 2A MVP` считается реализованным, если:

- система поднимается локально без отдельного backend API;
- `Streamlit` UI позволяет задавать вопросы и видеть ответ с evidence;
- explicit document queries находят нужный документ;
- explicit locator queries находят нужный фрагмент;
- вопросы без явной нормы не отклоняются автоматически;
- ответы строятся только по локальному evidence;
- unsupported claims отсекаются;
- retrieval использует `retrieval_units`, а не dense embeddings на всех `document_nodes`;
- DSPy layer изолирован от retrieval engine;
- провайдер моделей изолирован и не протекает в retrieval core.

---

## 10. Оценка качества MVP

Для первой версии достаточно компактного, но реального eval-набора.

Минимальные требования к оценке:

- локальный eval-набор на `50-100` вопросов;
- отдельные группы:
  - explicit document
  - explicit locator
  - no explicit norm
  - ambiguous queries

Минимальные метрики:

- `document hit@3`
- `locator hit@5`
- `grounded answer rate`
- `unsupported claim rate`
- `partial answer rate`
- доля инженерно-нормативных запросов без полного отказа

Для MVP оценка считается приемлемой, если:

- система стабильно находит документы по явным кодам и алиасам;
- умеет находить нужные локаторы без ручных подсказок;
- не сваливается в confident hallucination;
- дает полезный partial answer, когда evidence недостаточно.

---

## 11. Правило для следующих итераций

После завершения MVP допускается отдельно рассматривать:

- richer critic loop;
- open web / trusted sources;
- внешний API;
- альтернативные провайдеры;
- DSPy `compile`, optimizers и systematic optimization поверх готового eval-набора;
- улучшение parser Stage 1 и более умную сегментацию документа.
