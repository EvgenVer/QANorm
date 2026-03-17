# Задачи проекта

## Текущее состояние

Репозиторий находится в состоянии:

- `Stage 1` реализован и используется как базовый слой данных;
- старый Stage 2 удален;
- `Stage 2A MVP` собран и проходит ручное тестирование;
- базовый eval-прогон на `150` вопросах уже выполнен;
- baseline-метрики первого полного eval: `document hit@3 = 0.74`, `locator hit@5 = 0.00`, `expected mode match rate = 0.66`, `partial answer rate = 0.1933`;
- финальные метрики после `H5` и чистого parallel eval `v5`: `document hit@3 = 0.9267`, `locator hit@5 = 1.00`, `grounded answer rate = 1.00`, `expected mode match rate = 0.7867`, `partial answer rate = 0.0467`, `wrong document rate = 0.00`;
- `Stage 2A MVP` проходит все зафиксированные acceptance thresholds и готов к приемке; открытых implementation-задач не осталось.
- следующий implementation-этап: `Stage 2B` с conversational memory, multi-session UI и потоковым debug trace;
- `Stage 2B` сознательно реализуется без авторизации и без сохранения chat sessions в БД; память живет только в `st.session_state` до reload браузера.

## Stage 1

### Блок S1. База и ingestion

- [x] Сохранить локальную нормативную базу как основной слой данных.
- [x] Сохранить ingestion pipeline, worker и raw storage.
- [x] Сохранить `document_nodes` как канонический structural layer.
- [x] Сохранить Stage 1 CLI, тесты и readiness-артефакты.

## Stage 2A MVP

### Блок A. Подготовка Stage 1 под retrieval

- [x] Добавить миграцию для таблицы `document_aliases`.
- [x] Добавить миграцию для полей `document_nodes.locator_raw`, `document_nodes.locator_normalized`, `document_nodes.heading_path`.
- [x] Добавить индекс по `document_nodes.locator_normalized`.
- [x] Добавить миграцию для таблицы `retrieval_units`.
- [x] Добавить ORM-модели и репозитории для `document_aliases` и `retrieval_units`.
- [x] Добавить тесты на миграции и базовые CRUD-операции новых сущностей.

### Блок B. Derived retrieval data

- [x] Реализовать builder алиасов документов из кодов, названий и ссылок.
- [x] Реализовать backfill `document_aliases`.
- [x] Реализовать builder `document_card` units.
- [x] Реализовать builder `semantic_block` units поверх диапазонов `document_nodes`.
- [x] Реализовать backfill `retrieval_units`.
- [x] Реализовать индексацию `text_tsv` для `retrieval_units`.
- [x] Реализовать preflight-оценку для backfill `embedding` по `retrieval_units`: количество embeddings, ориентировочное количество токенов, оценку стоимости API, ожидаемый объем хранения в БД.
- [x] Подготовить краткий отчет по preflight-оценке embeddings и передать его пользователю на одобрение перед запуском генерации.
- [x] Реализовать отдельный фоновый resumable-процесс для backfill `embedding` с чекпоинтами, промежуточными сохранениями, возможностью продолжения после прерывания и логированием в файл.
- [x] Добавить CLI-команду запуска/возобновления фонового backfill `embedding` без блокировки основной разработки.
- [x] После одобрения пользователя запускать backfill `embedding` в фоне и продолжать реализацию задач, не требующих готового dense-слоя.
- [x] Добавить CLI-команды подготовки и пересборки derived retrieval data.
- [x] Добавить unit/integration tests на сборку `retrieval_units`.

### Блок C. Retrieval engine

- [x] Продолжать реализацию retrieval engine по задачам, не зависящим от готовых `embedding`, пока фоновый backfill не завершен.
- [x] Реализовать детерминированный parser вопроса.
- [x] Реализовать `resolve_document` по коду, алиасу и сокращению.
- [x] Реализовать `discover_documents` для вопросов без явной нормы.
- [x] Реализовать `lookup_locator`.
- [x] Реализовать lexical retrieval по `document_nodes` и `retrieval_units`.
- [x] Реализовать dense retrieval по `retrieval_units` после готовности фонового backfill `embedding`.
- [x] Реализовать merge и rerank shortlist.
- [x] Реализовать `read_node` и `expand_neighbors`.
- [x] Реализовать context builder и compact evidence pack.
- [x] Добавить unit/integration tests на retrieval engine.

### Блок D. DSPy layer, provider layer и contracts

- [x] Добавить Pydantic-схемы запросов, observations, evidence и answer DTO.
- [x] Добавить DSPy bootstrap для `ControllerAgent`, `Composer`, `GroundingVerifier`.
- [x] Добавить provider abstraction и конфигурацию модельного bootstrap.
- [x] Реализовать Gemini-конфигурацию для DSPy runtime.
- [x] Добавить retries, timeouts и обработку ошибок провайдера.
- [x] Добавить тесты на DSPy/provider bootstrap.

### Блок E. Agent runtime

- [x] Реализовать DSPy-based `ControllerAgent`.
- [x] Реализовать DSPy `ReAct-lite` loop с ограничением по шагам.
- [x] Подключить кастомные retrieval tools к DSPy runtime.
- [x] Реализовать policy выбора retrieval tools.
- [x] Реализовать stop conditions и corrective iteration policy.
- [x] Реализовать переход в partial mode при слабом evidence.
- [x] Добавить unit/integration tests на runtime.

### Блок F. Answer layer

- [x] Реализовать DSPy-based `Composer`.
- [x] Реализовать DSPy-based `GroundingVerifier`.
- [x] Реализовать claim-to-evidence mapping.
- [x] Реализовать фильтрацию unsupported statements.
- [x] Реализовать финальный answer model с citations и ограничениями ответа.
- [x] Добавить unit/integration tests на answer layer.

### Блок G. Streamlit MVP

- [x] Реализовать chat-first интерфейс на `Streamlit`.
- [x] Реализовать потоковый вывод ответа.
- [x] Реализовать панель evidence.
- [x] Реализовать отображение документа, локатора и цитаты.
- [x] Реализовать debug view шагов `ReAct-lite`.
- [x] Добавить локальный smoke checklist для ручной приемки UI.

### Блок H. Eval и приемка MVP

- [x] Собрать локальный eval-набор из `150` реальных вопросов.
- [x] Сделать основной фокус eval-набора на инженерных вопросах, в которых система должна сама понять, какие нормы искать.
- [x] Разбить eval-набор на `no explicit norm engineering`, `explicit document without locator`, `compact alias / dirty input`, `ambiguous scenarios` и небольшой hidden diagnostic slice для locator retrieval.
- [x] Не использовать точные пункты/таблицы/формулы как основной тип eval-вопросов; оставить их только в техническом diagnostic sub-set.
- [x] Реализовать прогон eval-набора и сбор метрик качества.
- [x] Зафиксировать `document hit@3`, `locator hit@5`, `grounded answer rate`, `unsupported claim rate`, `partial answer rate`.
- [x] Зафиксировать eval-набор как основу для следующей итерации DSPy optimization.
- [x] Подготовить краткий MVP readiness report.

### Блок H1. Исправление document ranking и edition drift

- [x] Усилить `resolve_document` и `discover_documents` за счет более агрессивной нормализации кодов, сокращений и алиасов.
- [x] Добавить в ranking явный приоритет актуальных редакций и penalty для legacy/устаревших документов, если найден современный СП/ГОСТ.
- [x] Ввести hard scope для запросов с явным документом: если пользователь спрашивает `СП 63`, retrieval не должен свободно уходить в соседние документы.
- [x] Добавить topic-to-document priors для провальных доменов eval: нагрузки/надежность, теплотехника, пожарные нормы, основания и фундаменты.
- [x] Обновить unit/integration tests на document resolution, edition ranking и compact aliases.

### Блок H2. Исправление locator-aware retrieval

- [x] Добавить отдельный путь exact/prefix lookup по `locator_normalized`, `heading_path` и связным locator-алиасам.
- [x] Если найден `document_node` с точным locator-hit, автоматически поднимать enclosing `retrieval_unit` как primary evidence вместо голого node-level ответа.
- [x] Усилить local context expansion для locator-hit: anchor node + enclosing unit + neighbors.
- [x] Пересобрать merge/rerank так, чтобы `retrieval_unit_locator` и `retrieval_unit_lexical` имели приоритет над `document_node_locator` как над semantic evidence.
- [x] Добавить unit/integration tests на hidden locator diagnostic slice и зафиксировать рост `locator hit@5`.

### Блок H3. Исправление interactive policy и answer mode

- [x] Добавить deterministic sufficiency check до `Composer`: document match, locator match, count `retrieval_unit` hits, count node-only hits, coverage по evidence.
- [x] Добавить ambiguity gate: слишком широкие вопросы должны переходить в `clarify`, а не в уверенный `direct`.
- [x] Ослабить downgrade в `partial`, если документ найден, `retrieval_unit` найден и evidence достаточен для прямого ответа.
- [x] Сделать limitations причинными и диагностичными: указывать, что именно ограничило ответ, а не общую формулировку.
- [x] Обновить tests на `expected mode match rate`, `partial answer rate` и сценарии `ambiguous_scenario`.

### Блок H4. Цикл повторной оценки и приемка

- [x] Прогнать targeted eval по сценариям `explicit document without locator`, `compact alias / dirty input`, `ambiguous_scenario`, `diagnostic_locator_hidden`.
- [x] Прогнать полный eval-набор на `150` вопросах после исправлений.
- [x] Сравнить baseline и post-fix метрики, зафиксировать прирост и оставшиеся провалы.
- [x] Если `document hit@3 < 0.85` или `locator hit@5 < 0.70` или `expected mode match rate < 0.75`, завести еще один короткий remediation-cycle до readiness report.
- [x] Подготовить итоговый MVP readiness report с финальными метриками, списком известных ограничений и рекомендациями для следующей итерации DSPy optimization.

### Блок H5. Corpus repair для отсутствующих canonical-документов

- [x] Провести forensic-разбор оставшихся провалов и отделить retrieval bugs от дефектов корпуса.
- [x] Зафиксировать, что `ГОСТ 27751-2014` и `СП 1.13130.2020` отсутствуют в canonical `documents`, а `SP 1.0` является placeholder-записью без source/raw linkage.
- [x] Убрать `SP 1.0` из retrieval candidate set и закрыть short-code prefix leakage вида `СП 1 -> СП 107`.
- [x] Подготовить targeted plan repair корпуса: удалить или изолировать placeholder `SP 1.0`, восстановить canonical ingest для `ГОСТ 27751-2014` и `СП 1.13130.2020`, затем пересобрать aliases и retrieval units для этих семейств.

## Stage 2B. Conversational Session Memory and UI

### Блок I. Session contracts and memory model

- [x] Добавить DTO `ConversationMessageDTO` для одного chat-сообщения.
- [x] Добавить DTO `ConversationMemoryDTO` для bounded session memory.
- [x] Добавить DTO `Stage2AChatSessionDTO` для одной локальной чат-сессии.
- [x] Добавить DTO `RuntimeEventDTO` для потоковых событий runtime.
- [x] Зафиксировать типы runtime events:
  - `query_received`
  - `query_rewritten`
  - `controller_started`
  - `tool_started`
  - `tool_finished`
  - `evidence_updated`
  - `composer_started`
  - `verifier_started`
  - `answer_ready`
  - `warning`
- [x] Определить bounded memory policy:
  - сколько последних сообщений хранить как raw transcript;
  - какой максимальный размер `conversation_summary`;
  - какие hints включать в memory.
- [x] Определить структуру `active_document_hints`.
- [x] Определить структуру `active_locator_hints`.
- [x] Определить структуру `open_threads`.
- [x] Определить правила обновления memory после `direct` ответа.
- [x] Определить правила обновления memory после `partial` ответа.
- [x] Определить правила обновления memory после `clarify` ответа.
- [x] Определить, как хранить `last_result` для повторного рендера evidence/debug без повторного запроса.
- [x] Добавить unit tests на DTO и memory normalization helpers.

### Блок J. Session-aware runtime

- [x] Расширить runtime API так, чтобы он принимал состояние активной чат-сессии.
- [x] Добавить отдельный входной объект `Stage2AConversationalQueryRequest`.
- [x] Реализовать выделение типа нового пользовательского сообщения:
  - новый вопрос;
  - follow-up;
  - уточнение;
  - просьба дополнить предыдущий ответ;
  - сброс/смена контекста внутри сессии.
- [x] Реализовать `effective query builder`.
- [x] Реализовать использование `conversation_summary` при построении effective query.
- [x] Реализовать использование последних сообщений при построении effective query.
- [x] Реализовать использование `active_document_hints` как soft prior.
- [x] Реализовать использование `active_locator_hints` как soft prior.
- [x] Реализовать использование `open_threads` для follow-up вопросов.
- [x] Сделать так, чтобы follow-up запросы не воспринимались как новый независимый вопрос с нуля.
- [x] Сделать так, чтобы запросы вида `дополни`, `продолжи`, `а что для ...`, `какой пункт?` использовали предыдущий ответ как контекст.
- [x] Реализовать повторный retrieval с учетом session memory.
- [x] Реализовать правило, при котором follow-up может расширять evidence pack, а не только переиспользовать старый.
- [x] Реализовать обновление `conversation_summary` после каждого ответа.
- [x] Реализовать обновление `active_document_hints` после каждого ответа.
- [x] Реализовать обновление `active_locator_hints` после каждого ответа.
- [x] Реализовать обновление `open_threads` после каждого ответа.
- [x] Сделать так, чтобы смена темы внутри одной сессии не блокировалась жесткими prior-ами предыдущего вопроса.
- [x] Добавить unit tests на классификацию follow-up/clarify/expand-message.
- [x] Добавить unit tests на `effective query builder`.
- [x] Добавить unit tests на memory update policy.
- [x] Добавить integration tests на conversational runtime flow без UI.

### Блок K. Runtime event streaming

- [ ] Добавить в runtime второй интерфейс `stream_answer_query(...)`.
- [ ] Сделать event streaming совместимым с уже существующим `answer_query(...)`.
- [ ] Реализовать генерацию события `query_received`.
- [ ] Реализовать генерацию события `query_rewritten`.
- [ ] Реализовать генерацию события `controller_started`.
- [ ] Реализовать генерацию события `tool_started`.
- [ ] Реализовать генерацию события `tool_finished`.
- [ ] Реализовать генерацию события `evidence_updated`.
- [ ] Реализовать генерацию события `composer_started`.
- [ ] Реализовать генерацию события `verifier_started`, если verifier реально участвует.
- [ ] Реализовать генерацию события `warning` для ограничений и fallback-веток.
- [ ] Реализовать генерацию финального события `answer_ready`.
- [ ] Сделать так, чтобы event stream не ломал основной synchronous API и тесты Stage 2A.
- [ ] Добавить unit tests на последовательность runtime events.
- [ ] Добавить unit tests на fallback-ветки event stream.

### Блок L. Multi-session UI state

- [x] Добавить в `Streamlit` sidebar для управления чат-сессиями.
- [x] Добавить `st.session_state.sessions`.
- [x] Добавить `st.session_state.active_session_id`.
- [x] Реализовать создание первой сессии по умолчанию при первом открытии UI.
- [x] Реализовать кнопку `Новая сессия`.
- [x] Реализовать генерацию локального `session_id`.
- [x] Реализовать human-readable title для новой сессии.
- [x] Реализовать переключение между сессиями в sidebar.
- [x] Реализовать кнопку `Сбросить текущую сессию`.
- [x] Сделать так, чтобы сброс очищал только активную сессию.
- [x] Сделать так, чтобы другие локальные сессии пользователя не затрагивались.
- [x] Сделать так, чтобы transcript, memory и last result были изолированы между локальными сессиями.
- [x] Убедиться, что никакое chat-state не живет в `@st.cache_resource`.
- [x] Добавить unit tests на session state helpers, если они будут вынесены в отдельный модуль.

### Блок M. Streamlit chat rendering and formatting

- [ ] Перестать стримить answer по словам через `split()`.
- [ ] Реализовать chunk-aware streaming, который сохраняет переносы строк.
- [ ] После завершения стрима всегда выполнять финальный `markdown()` render полного ответа.
- [ ] Разделить визуально debug stream и финальный answer render.
- [ ] Сделать так, чтобы debug trace стримился в assistant message во время ответа.
- [ ] Сделать так, чтобы после завершения ответа debug trace автоматически сворачивался.
- [ ] Оставить пользователю возможность раскрыть debug trace вручную.
- [ ] Сохранить финальный `result` в session state активной сессии.
- [ ] Исправить рендер evidence panel в рамках активной сессии.
- [ ] Исправить рендер limitations в рамках активной сессии.
- [ ] Убедиться, что formatting не требует следующего вопроса для нормального отображения.
- [ ] Добавить smoke tests / scripted checks на markdown rendering helpers, если они будут вынесены из `app.py`.

### Блок N. Conversational retrieval behavior

- [ ] Реализовать policy follow-up retrieval для вопросов, продолжающих предыдущую тему.
- [ ] Реализовать policy для уточняющих вопросов по уже найденному документу.
- [ ] Реализовать policy для вопросов `какой пункт?`, `где это написано?`, `приведи ссылку`.
- [ ] Реализовать policy для вопросов `дополни ответ`, `продолжи`, `что еще`.
- [ ] Реализовать policy, при которой новый follow-up может поднимать дополнительные locator hits и retrieval units.
- [ ] Реализовать policy, при которой предыдущий `partial` ответ может стать `direct` после follow-up.
- [ ] Реализовать policy смены контекста, если пользователь явно уводит разговор в другой документ или другую тему.
- [ ] Добавить unit/integration tests на сценарии:
  - follow-up без повторного упоминания нормы;
  - уточнение по найденному документу;
  - дополнение предыдущего partial ответа;
  - переход к новой теме внутри той же сессии.

### Блок O. Manual acceptance and regression coverage

- [ ] Обновить ручной smoke checklist под conversational UI.
- [ ] Добавить ручной сценарий `вопрос -> follow-up -> уточнение`.
- [ ] Добавить ручной сценарий `partial -> дополни ответ`.
- [ ] Добавить ручной сценарий `Новая сессия`.
- [ ] Добавить ручной сценарий `две независимые сессии в одном UI`.
- [ ] Добавить ручной сценарий `debug trace стримится в чат`.
- [ ] Добавить ручной сценарий `форматирование корректно без следующего вопроса`.
- [ ] Добавить integration tests на изоляцию session state между несколькими локальными чатами.
- [ ] Подготовить краткий readiness report по `Stage 2B`.

## Покрытие плана

- Блок `A` покрывает подготовку Stage 1 под Stage 2A.
- Блок `B` покрывает построение derived retrieval data.
- Блок `C` покрывает retrieval engine.
- Блок `D` покрывает DSPy/provider bootstrap и contracts.
- Блок `E` покрывает DSPy `ReAct-lite` runtime.
- Блок `F` покрывает DSPy answer layer.
- Блок `G` покрывает `Streamlit` MVP.
- Блок `H` покрывает базовую приемку, eval-набор и фиксацию baseline-метрик.
- Блок `H1` покрывает document ranking, edition drift и compact alias remediation.
- Блок `H2` покрывает locator-aware retrieval remediation.
- Блок `H3` покрывает interactive policy, clarify policy и answer-mode remediation.
- Блок `H4` покрывает повторный eval-цикл, сравнение baseline/post-fix и итоговую приемку.
- Блок `I` покрывает contracts и bounded memory model для Stage 2B.
- Блок `J` покрывает session-aware runtime и effective query logic.
- Блок `K` покрывает runtime event streaming.
- Блок `L` покрывает multi-session UI state.
- Блок `M` покрывает chat rendering, streaming trace и markdown-formatting fix.
- Блок `N` покрывает conversational retrieval behavior.
- Блок `O` покрывает ручную приемку и regression coverage для Stage 2B.
