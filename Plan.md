# План реализации

## 1. Назначение

Документ фиксирует практический план следующего этапа развития QANorm после завершения `Stage 2A MVP`.

Текущая цель:

- не перестраивать `Stage 2A` заново;
- не добавлять лишнюю инфраструктуру;
- быстро получить conversational-режим поверх уже работающего `Stage 2A`;
- добавить session memory, follow-up вопросы, multi-chat UI и потоковый debug trace;
- сохранить текущую простую модель развертывания: `Streamlit + PostgreSQL + DSPy`.

---

## 2. Текущая точка

На начало этого этапа:

- `Stage 1` реализован и используется как corpus/source of truth;
- `Stage 2A MVP` реализован и принят по eval-метрикам;
- retrieval, agent runtime, answer layer и `Streamlit` UI уже работают;
- новый этап не меняет retrieval corpus layer и не требует новой БД-схемы.

Иными словами:

- retrieval core уже есть;
- agent runtime уже есть;
- нужно добавить conversational layer и улучшить UX работы с чатом.

---

## 3. Границы нового этапа

Новый этап трактуется как `Stage 2B`.

Входит в `Stage 2B`:

- память в пределах текущей браузерной сессии;
- follow-up и уточняющие вопросы;
- повторный retrieval с учетом истории чата;
- несколько независимых чатов в одном UI;
- кнопка новой сессии и сброса текущей;
- потоковый debug/runtime trace прямо в чат;
- исправление markdown-форматирования при потоковом выводе;
- изоляция разных пользователей через `Streamlit session_state`.

Не входит в `Stage 2B`:

- авторизация;
- хранение сессий в PostgreSQL;
- восстановление чатов после reload браузера;
- общий storage сессий между пользователями;
- Redis, Celery, websocket backend, отдельный API;
- переписывание Stage 1 или Stage 2A retrieval architecture.

---

## 4. Основные архитектурные решения

### 4.1. Session memory только в UI state

Память чатов хранится только в `st.session_state`.

Это означает:

- chat sessions живут, пока жива текущая browser session;
- после reload страницы все начинается с нуля;
- не вводится новый persistent storage слой;
- не добавляются новые таблицы в PostgreSQL.

### 4.2. Несколько чатов на пользователя

Один пользователь в одной browser session должен иметь несколько независимых чатов.

Минимальная структура состояния:

- `active_session_id`
- `sessions`

Каждая session должна содержать:

- `messages`
- `conversation_summary`
- `active_document_hints`
- `active_locator_hints`
- `open_threads`
- `last_result`

### 4.3. Session-aware runtime

`Stage2ARuntime` больше не должен считать каждое сообщение новым независимым запросом.

Runtime должен:

- принимать состояние активной чат-сессии;
- строить `effective query` с учетом памяти;
- различать новый вопрос, follow-up, уточнение и просьбу дополнить ответ;
- повторно запускать retrieval с учетом накопленного контекста;
- обновлять memory после каждого ответа.

### 4.4. Bounded memory, не полный transcript

В модель не должен уходить весь transcript целиком.

Используется bounded-подход:

- последние `N` сообщений;
- краткий `conversation_summary`;
- document/locator hints;
- `open_threads`.

Это нужно, чтобы:

- не раздувать prompt;
- не ломать latency;
- не деградировать качество на длинных чатах.

### 4.5. Streaming runtime events

Runtime должен уметь работать в двух режимах:

- обычный `answer_query(...)` для тестов и синхронного вызова;
- `stream_answer_query(...)` для UI.

Во время ответа runtime должен стримить события:

- parse/query rewrite;
- tool started / tool finished;
- shortlist changes;
- evidence updated;
- composer started;
- final answer ready.

### 4.6. UI debug trace как часть чата

Debug trace больше не должен появляться только после завершения ответа.

Новый UX:

- во время выполнения запроса debug trace стримится в assistant message;
- после завершения ответа trace автоматически сворачивается;
- пользователь может раскрыть его вручную.

### 4.7. Исправление форматирования

Проблема текущего UI связана с тем, что потоковый ответ режется слишком грубо и финальный markdown-render не всегда выполняется в том же UI-cycle.

Новая политика:

- стримить не по словам, а chunk-aware способом;
- после завершения ответа всегда делать финальный `markdown()` render полного текста;
- debug stream и final answer render разделять визуально.

### 4.8. Изоляция нескольких пользователей

На этом этапе изоляция пользователей обеспечивается самим `Streamlit`.

Требования:

- нельзя хранить chat memory в process-wide mutable singleton;
- можно кэшировать только shared-safe объекты:
  - runtime factory
  - provider bootstrap
  - config
  - stateless retrieval components
- chat state остается только внутри `st.session_state`.

---

## 5. Принятый стек

Стек не меняется и остается таким же, как в `Stage 2A`:

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
- `pytest`
- `respx`

Что сознательно не добавляем:

- `Redis`
- `FastAPI`
- `websocket backend`
- отдельный persistent chat store

---

## 6. Структура проекта и модули

### 6.1. Сохраняемые модули

Без концептуальных изменений сохраняются:

- `src/qanorm/stage2a/retrieval`
- `src/qanorm/stage2a/agents`
- `src/qanorm/stage2a/providers`
- `src/qanorm/stage2a/contracts`
- `src/qanorm/stage2a/runtime`
- `src/qanorm/stage2a/ui`

### 6.2. Новые или расширяемые модули Stage 2B

- `src/qanorm/stage2a/contracts`
  - новые DTO для session memory, chat message, runtime events
- `src/qanorm/stage2a/runtime`
  - session-aware orchestration
  - `effective query`
  - memory update
  - event streaming
- `src/qanorm/stage2a/ui/app.py`
  - multi-session UI
  - new session / reset session
  - streaming debug trace
  - финальный markdown render
- `src/qanorm/stage2a/ui`
  - helper-функции рендера chat sessions и event trace

Опционально можно выделить:

- `src/qanorm/stage2a/session_memory.py`
  - чистая логика памяти и query rewrite
- `src/qanorm/stage2a/events.py`
  - runtime event models и helpers

---

## 7. План реализации по блокам

### Блок B1. Session contracts and memory model

Сделать минимальную модель памяти.

Задачи:

- добавить DTO для chat messages;
- добавить DTO для session state;
- добавить DTO для runtime events;
- определить bounded memory policy;
- определить правила обновления `conversation_summary`, `active_document_hints`, `active_locator_hints`, `open_threads`.

Результат:

- есть формализованная session memory model.

### Блок B2. Session-aware runtime

Научить runtime работать не с одним текстом вопроса, а с контекстом активной сессии.

Задачи:

- расширить runtime API;
- добавить `effective query` / follow-up rewrite;
- добавить определение типа сообщения:
  - new question
  - follow-up
  - clarify
  - expand previous answer
- повторно запускать retrieval с учетом memory hints;
- обновлять session memory после ответа.

Результат:

- follow-up сообщения перестают быть независимыми вопросами с нуля.

### Блок B3. Multi-session UI

Добавить несколько чатов в одном `Streamlit` UI.

Задачи:

- sidebar со списком сессий;
- `Новая сессия`;
- `Сбросить текущую сессию`;
- переключение между сессиями;
- хранение session state только в `st.session_state`.

Результат:

- пользователь может вести несколько независимых бесед.

### Блок B4. Streaming debug trace

Вывести промежуточные шаги агента прямо в чат.

Задачи:

- добавить event-stream API в runtime;
- начать стримить parse/retrieval/evidence/composer events;
- показывать trace в assistant message во время ответа;
- после завершения ответа автоматически сворачивать debug trace.

Результат:

- в UI видно, как работает агент в процессе ответа.

### Блок B5. UI formatting fix

Исправить потоковый рендер markdown.

Задачи:

- убрать streaming по словам;
- стримить chunk-aware;
- после завершения ответа выполнять финальный полный `markdown()` render;
- проверить корректное отображение переносов, списков и блоков текста.

Результат:

- форматирование ответа корректно отображается сразу.

### Блок B6. Tests and manual acceptance

Добавить проверки нового conversational-режима.

Задачи:

- unit tests на session memory logic;
- unit tests на follow-up rewrite;
- unit tests на session switching/reset;
- integration tests на conversational flow;
- ручной smoke checklist для:
  - follow-up
  - дополнение partial answer
  - новая сессия
  - несколько сессий
  - streaming debug trace
  - markdown formatting

Результат:

- есть подтверждение, что `Stage 2B` работает стабильно.

---

## 8. Критерии приемки Stage 2B

Этап считается выполненным, если:

- follow-up вопросы используют предыдущий контекст;
- запросы вида `дополни`, `а что для ...`, `уточни` не стартуют reasoning с нуля;
- retrieval может дообогащать evidence на следующем сообщении;
- `Новая сессия` создает пустой чат без контекста предыдущего;
- сброс текущей сессии очищает только ее;
- в одной browser session можно вести несколько независимых чатов;
- параллельные пользователи не делят состояние между собой;
- debug trace стримится в чат во время ответа;
- после завершения ответа trace сворачивается;
- markdown-форматирование не требует следующего вопроса для нормального отображения.

---

## 9. Что сознательно откладывается

После завершения `Stage 2B` можно отдельно рассматривать:

- persistent chat sessions в БД;
- авторизацию пользователей;
- восстановление чатов после reload;
- richer conversation memory;
- long-term memory;
- отдельный backend API;
- multi-user shared session storage.
