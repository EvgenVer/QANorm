# Спецификация проекта

## 1. Общая цель

QANorm развивается в два слоя:

1. `Stage 1`: локальная нормативная база документов, ingestion, хранение, нормализация и индексация.
2. `Stage 2A`: упрощенный `Agentic RAG v1` поверх локальной базы Stage 1.

На текущем этапе цель проекта:

- сохранить и использовать уже собранную локальную нормативную базу;
- быстро построить рабочий `MVP` консультативного слоя;
- отвечать на инженерные вопросы только по локальному нормативному корпусу;
- не усложнять первую реализацию лишней инфраструктурой и тяжелыми framework-core в retrieval слое.

---

## 2. Stage 1. Локальная нормативная база

### 2.1. Назначение Stage 1

Stage 1 отвечает за:

- обход утвержденных seed-разделов;
- извлечение карточек документов и метаданных;
- загрузку raw-артефактов;
- извлечение текста из HTML и PDF;
- OCR как fallback;
- нормализацию документа в структуру `document_nodes`;
- хранение документов, версий, источников, ссылок и истории обновлений;
- построение локальных индексов для последующего retrieval.

### 2.2. Сохраняемый базовый слой данных

Stage 2A обязан использовать Stage 1 как source of truth. Базовыми сущностями остаются:

- `documents`
- `document_versions`
- `document_sources`
- `raw_artifacts`
- `document_nodes`
- `document_references`
- `ingestion_jobs`
- `update_events`

### 2.3. Роль `document_nodes`

`document_nodes` остаются каноническим структурным слоем документа.

`document_nodes` должны использоваться для:

- иерархии документа;
- точного поиска по локаторам;
- полнотекстового поиска;
- восстановления соседнего контекста;
- построения цитат и ссылок в финальном ответе.

`document_nodes` не должны использоваться как основная semantic unit для dense retrieval в Stage 2A.

### 2.4. Минимальные доработки Stage 1 для Stage 2A

Для запуска Stage 2A допускаются только минимальные изменения локальной базы:

- добавить таблицу `document_aliases` для сокращений, альтернативных обозначений и старых кодов документов;
- добавить в `document_nodes` поля:
  - `locator_raw`
  - `locator_normalized`
  - `heading_path`
- добавить индекс по `locator_normalized`;
- сохранить node-level `text_tsv`;
- не пересобирать Stage 1 parser радикально и не менять базовую разбивку `document_nodes` в первой итерации.

### 2.5. Dense retrieval слой Stage 2A

Dense retrieval не должен строиться на всех `document_nodes`.

Для dense retrieval должен использоваться отдельный derived слой `retrieval_units`, сформированный поверх Stage 1.

Минимальные сущности Stage 2A:

- `document_aliases`
- `retrieval_units`

`retrieval_units` должны включать как минимум:

- `document_card` для semantic document discovery;
- `semantic_block` для dense retrieval внутри документа или shortlist документов.

### 2.6. Правила формирования `retrieval_units`

`retrieval_units` должны формироваться детерминированно из Stage 1 данных.

#### `document_card`

Одна semantic единица на `document_version`, содержащая:

- код документа;
- название;
- краткое описание или scope;
- ключевые заголовки;
- алиасы документа.

`document_card` используется для `discover_documents`.

#### `semantic_block`

`semantic_block` формируется из нескольких соседних `document_nodes`.

Правила формирования:

- блок строится по порядку `order_index` внутри документа;
- короткие соседние leaf-узлы объединяются в одну semantic единицу;
- блок разрывается при смене крупного структурного контекста;
- блок не должен быть слишком мелким;
- блок должен хранить ссылку назад на исходный диапазон узлов.

`semantic_block` должен хранить как минимум:

- `document_version_id`
- `unit_type`
- `anchor_node_id`
- `start_order_index`
- `end_order_index`
- `heading_path`
- `locator_primary`
- `text`
- `text_tsv`
- `embedding`
- `chunk_hash`

---

## 3. Stage 2A. Agentic RAG v1

### 3.1. Назначение Stage 2A

Stage 2A реализует быстрый `MVP` консультативного слоя поверх локальной базы Stage 1.

Система должна:

- принимать инженерный вопрос пользователя;
- находить релевантные нормативные документы и фрагменты в локальной базе;
- выполнять ограниченный `ReAct-lite` цикл поиска;
- формировать grounded answer только по найденному evidence;
- сопровождать ответ citations;
- работать через локальный интерфейс `Streamlit`.

### 3.2. Принятые решения по бизнес-логике

В Stage 2A зафиксированы следующие правила:

- система отвечает только по локальной нормативной базе;
- если вопрос выглядит инженерно-нормативным, retrieval запускается всегда;
- отсутствие явного упоминания СП, ГОСТ или локатора не является причиной отказа;
- если evidence слабое, система возвращает partial answer или shortlist вероятных норм, а не молчит;
- финальный ответ не должен содержать unsupported claims;
- приоритет имеет прямой нормативный текст, а не косвенные интерпретации;
- в первой версии не используются open web, trusted web, Telegram и внешний API.

### 3.3. Архитектурные принципы

Stage 2A должен строиться по следующим принципам:

- `DSPy-hybrid`: кастомный retrieval/data layer и DSPy только для `ControllerAgent`, `Composer` и `GroundingVerifier`;
- `ReAct-lite`: один управляющий агент с ограниченным набором tools;
- `Adaptive retrieval`: стратегия поиска зависит от вопроса и качества найденного evidence;
- `Hybrid retrieval`: exact lookup + locator lookup + lexical search + dense retrieval + rerank;
- `Document-aware retrieval`: при явном документе сначала поиск внутри документа;
- `Locator-aware retrieval`: при наличии локатора сначала lookup по локатору;
- `Grounded answer only`: ответ строится только по evidence pack;
- `Bounded loop`: не более 2 corrective итераций и не более 4-6 tool steps;
- `Small evidence pack`: в генерацию передаются только лучшие 5-8 evidence-блоков;
- `Provider abstraction`: замена модельного провайдера должна делаться конфигом без переписывания retrieval engine.

### 3.4. Границы первой версии

В Stage 2A входят:

- локальная база Stage 1;
- `Streamlit` UI;
- controller agent;
- document discovery;
- document resolution;
- locator lookup;
- lexical retrieval;
- dense retrieval по `retrieval_units`;
- reranking shortlist;
- DSPy-based `ControllerAgent`;
- DSPy-based `Composer`;
- DSPy-based `GroundingVerifier`.

В Stage 2A не входят:

- open web search;
- trusted sources;
- Telegram;
- отдельный backend API;
- distributed multi-agent runtime;
- long-term memory;
- сложный audit/security runtime;
- тяжелая перестройка Stage 1 parser;
- dense embeddings на всех `document_nodes`.

### 3.5. Технологический стек

Для Stage 2A утвержден следующий стек:

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

Первая версия должна использовать `Gemini` как первый провайдер через `API key` и DSPy/provider bootstrap, без vendor SDK в бизнес-коде.

### 3.6. Целевая архитектура

Stage 2A состоит из четырех слоев:

- `UI Layer`: `Streamlit` chat-first интерфейс;
- `DSPy Layer`: `ControllerAgent`, `Composer`, `GroundingVerifier`;
- `Retrieval Engine`: parser, resolver, discoverer, lexical retrieval, dense retrieval, reranker, context builder;
- `Corpus Layer`: PostgreSQL база Stage 1, raw storage и derived слой `retrieval_units`.

### 3.7. ReAct-lite runtime

В системе должен быть один основной агент `ControllerAgent`, реализованный на DSPy.

`ControllerAgent` обязан:

- анализировать вопрос;
- выбирать следующий retrieval tool;
- получать observation;
- решать, достаточно ли evidence;
- при необходимости запускать еще один retrieval step;
- передавать финальный evidence pack в `Composer`.

`ControllerAgent` должен использовать DSPy как execution layer, но retrieval tools должны оставаться кастомными функциями проекта.

Допустимые tools v1:

- `resolve_document`
- `discover_documents`
- `lookup_locator`
- `search_lexical`
- `search_semantic`
- `read_node`
- `expand_neighbors`

Ограничения:

- агент не отвечает до evidence-producing шага;
- agent loop ограничен по числу шагов;
- corrective loop ограничен максимум двумя итерациями;
- при слабом evidence ответ должен быть переведен в partial mode.

### 3.8. Поведение для вопросов без явного указания нормы

Если пользователь не указал документ, редакцию или локатор, но вопрос распознается как инженерно-нормативный, система обязана:

- не отклонять запрос автоматически;
- выделить topic hints, object hints и constraints;
- выполнить `discover_documents`;
- построить shortlist наиболее вероятных документов;
- выполнить scoped retrieval внутри shortlist;
- сформировать ответ по найденному evidence, если confidence достаточен.

Если confidence недостаточен, система должна:

- не молчать;
- вернуть partial answer или shortlist вероятных норм;
- явно указать неопределенность;
- при необходимости запросить уточнение контекста.

### 3.9. Retrieval pipeline

Retrieval pipeline v1 должен работать так:

1. Детерминированный разбор запроса.
2. Извлечение hints: document code, locator, topic, constraints.
3. Если документ указан явно, запуск `resolve_document`.
4. Если документ не указан, запуск `discover_documents`.
5. Если найден документ или shortlist, запуск scoped retrieval.
6. Если документ не распознан, запуск global hybrid retrieval.
7. Слияние lexical и dense кандидатов.
8. Rerank shortlist.
9. Расширение соседних `document_nodes` для локального контекста.
10. Сбор компактного evidence pack.
11. Передача evidence pack в `Composer`.
12. Проверка ответа через `GroundingVerifier`.

### 3.10. Provider abstraction

Архитектура должна разделять два уровня модельной интеграции:

- DSPy layer для `ControllerAgent`, `Composer`, `GroundingVerifier`;
- custom provider interfaces для retrieval-related model calls, если они нужны отдельно.

Базовые capability-based интерфейсы:

- `ChatModel`
- `ToolCallingModel`
- `EmbeddingModel`
- `RerankModel`
- `StructuredOutputModel`

Требования:

- DSPy layer не должен содержать vendor-specific логики в бизнес-коде;
- интеграция Gemini должна инкапсулироваться в отдельной конфигурации LM bootstrap;
- retrieval engine не должен зависеть от DSPy внутренних типов;
- провайдер должен меняться через конфиг;
- позднее должна быть возможна замена на другой adapter или локальную модель без переписывания retrieval слоя.

### 3.11. Генерация ответа

`Composer` должен быть реализован как DSPy module и должен:

- использовать только evidence pack;
- возвращать краткий вывод;
- показывать citations;
- явно указывать документ, локатор и короткий evidence fragment;
- указывать ограничения ответа, если coverage неполное.

`GroundingVerifier` должен быть реализован как DSPy module и должен:

- проверять привязку тезисов к evidence ids;
- удалять unsupported statements;
- переводить ответ в partial mode при недостаточном evidence.

### 3.12. Streamlit UI

Первая версия интерфейса должна быть реализована на `Streamlit`.

Интерфейс должен включать:

- ввод вопроса;
- потоковый вывод ответа;
- панель evidence;
- отображение документа, локатора и цитаты;
- опциональный debug view шагов `ReAct-lite`.

Ограничения v1:

- одна локальная пользовательская сессия;
- без отдельного backend API;
- без Telegram;
- без multi-user persistence.

### 3.13. Критерии приемки

Stage 2A считается готовым к MVP, если система:

- отвечает только по локальной нормативной базе;
- умеет находить документы по явным кодам, алиасам и сокращениям;
- умеет находить фрагменты по локаторам;
- умеет работать с вопросами без явного указания нормы;
- выполняет hybrid retrieval с dense слоем на `retrieval_units`;
- формирует grounded answer с citations;
- не выдает unsupported confident claims;
- работает через `Streamlit`;
- допускает замену модельного провайдера через DSPy-конфиг и provider abstraction.

### 3.14. Минимальная оценка качества MVP

Для оценки качества MVP нужен локальный eval-набор из реальных вопросов.

Минимальный объем первого eval-набора:

- 50-100 вопросов.

Обязательные метрики:

- `document hit@3` для вопросов с явным документом;
- `locator hit@5` для вопросов с явным локатором;
- `grounded answer rate`;
- `unsupported claim rate`;
- `partial answer rate`;
- доля запросов, на которые система ответила без полного молчания.

### 3.15. Вне рамок MVP

За рамками Stage 2A остаются:

- open web search;
- trusted sources;
- richer critic loop;
- отдельный backend API;
- Telegram;
- долгоживущие сессии;
- сложная observability для консультативного runtime;
- использование `LangChain`, `LangGraph` или `LlamaIndex` как retrieval/runtime core;
- DSPy `compile` и optimizer flows как обязательная часть MVP.
