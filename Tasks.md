# Задачи проекта

## Текущее состояние

Репозиторий находится в состоянии:

- `Stage 1` реализован и используется как базовый слой данных;
- старый Stage 2 удален;
- `Stage 2A MVP` собран и проходит ручное тестирование;
- базовый eval-прогон на `150` вопросах уже выполнен;
- текущие baseline-метрики: `document hit@3 = 0.74`, `locator hit@5 = 0.00`, `expected mode match rate = 0.66`, `partial answer rate = 0.1933`;
- следующий шаг проекта: исправить критические провалы eval и довести MVP до приемки.

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
- [ ] Подготовить краткий MVP readiness report.

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
- [ ] Сделать limitations причинными и диагностичными: указывать, что именно ограничило ответ, а не общую формулировку.
- [x] Обновить tests на `expected mode match rate`, `partial answer rate` и сценарии `ambiguous_scenario`.

### Блок H4. Цикл повторной оценки и приемка

- [ ] Прогнать targeted eval по сценариям `explicit document without locator`, `compact alias / dirty input`, `ambiguous_scenario`, `diagnostic_locator_hidden`.
- [ ] Прогнать полный eval-набор на `150` вопросах после исправлений.
- [ ] Сравнить baseline и post-fix метрики, зафиксировать прирост и оставшиеся провалы.
- [ ] Если `document hit@3 < 0.85` или `locator hit@5 < 0.70` или `expected mode match rate < 0.75`, завести еще один короткий remediation-cycle до readiness report.
- [ ] Подготовить итоговый MVP readiness report с финальными метриками, списком известных ограничений и рекомендациями для следующей итерации DSPy optimization.

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
