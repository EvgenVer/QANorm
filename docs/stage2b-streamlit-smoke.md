# Stage 2B Streamlit Manual Acceptance Checklist

## 1. Preparation

1. Start PostgreSQL and the UI:
   - `docker start qanorm-pg16`
   - `streamlit run src/qanorm/stage2a/ui/app.py`
2. Open the local Streamlit URL.
3. Confirm the page shows:
   - header `QANorm Stage 2A`;
   - sidebar with session controls;
   - an empty chat and input box.

Expected result:
- the page loads without traceback;
- the sidebar shows one local chat session by default;
- the current session starts with empty transcript and empty debug state.

## 2. Question -> Follow-up -> Clarification

Steps:
1. Ask: `Что СП 63 говорит про максимальный шаг арматуры в плитах?`
2. After the answer finishes, ask: `А для плит толщиной больше 150 мм?`
3. Then ask: `Какой именно пункт это устанавливает?`

Check:
- the second question is treated as a continuation, not as a brand new topic;
- the third question reuses the active document and locator hints;
- the final answer contains a more specific citation than the first answer;
- `Evidence` is updated between turns.

Expected result:
- the agent stays inside the same document family unless the user explicitly changes topic;
- follow-up questions reuse session memory and can refine the evidence pack;
- locator or heading information becomes more specific after the clarification.

## 3. Partial -> Expand Answer

Steps:
1. Ask a broad question that normally produces `partial`, for example:
   `Какие требования к рабочим швам в бетоне?`
2. After a partial answer, ask:
   `Дополни ответ и приведи больше контекста`

Check:
- the second turn uses the previous answer as context;
- the new answer is longer or more specific than the original one;
- the evidence panel grows or changes, instead of simply repeating the same snippet;
- if the answer stays partial, the limitations explain what is still missing.

Expected result:
- the follow-up can promote a previous partial answer to a fuller answer;
- the agent performs another retrieval pass instead of returning the old answer verbatim.

## 4. New Session

Steps:
1. In an active chat with existing messages, click `Новая сессия`.
2. Verify the UI switches to a new local session.

Check:
- the transcript area becomes empty;
- the new session has no inherited memory, no evidence panel, and no last result;
- the previous session still exists in the sidebar.

Expected result:
- a new isolated chat starts from zero;
- the prior session is preserved and can be reopened later in the same browser session.

## 5. Two Independent Sessions in One UI

Steps:
1. In session A, ask a question about `СП 63`.
2. Create session B and ask a question about fire safety or evacuation.
3. Switch back to session A.

Check:
- session A still contains only the concrete/reinforcement thread;
- session B contains only the fire-safety thread;
- switching sessions does not mix chat history, evidence, debug trace, or limitations.

Expected result:
- local sessions are isolated from each other inside one browser session;
- active document hints from session A do not leak into session B and vice versa.

## 6. Debug Trace Streams Into Chat

Steps:
1. Ask any non-trivial question.
2. While the answer is being generated, watch the assistant message area.

Check:
- a debug block appears before the final answer is ready;
- tool steps and evidence updates appear during generation;
- after completion the debug block collapses by default;
- the user can reopen it manually.

Expected result:
- the runtime trace is visible during answer generation;
- the final answer stays readable and the debug trace remains available on demand.

## 7. Formatting Is Correct Without a Second Question

Steps:
1. Ask a question that produces a multi-paragraph answer with bullets or citations.
2. Do not ask anything else.
3. Inspect the answer immediately after the stream completes.

Check:
- line breaks are preserved;
- bullet lists render as lists;
- citations do not appear as a single flattened line;
- the final render is already correct before the next rerun.

Expected result:
- the answer formatting is correct immediately after the first render;
- no second prompt is required to “fix” markdown layout.

## 8. Failure Signals

The scenario is failed if at least one of these happens:
- the UI crashes with traceback;
- a follow-up is treated as an unrelated new question without explicit topic change;
- `Новая сессия` clears another session;
- two local sessions show mixed messages or mixed evidence;
- debug trace does not appear until the answer is already complete;
- markdown is flattened into one line until the next user message.

## 9. Minimum Acceptance Result

Stage 2B manual acceptance passes if:
- follow-up and clarification questions reuse session context correctly;
- a partial answer can be expanded in a later turn;
- `Новая сессия` creates a clean local chat without touching others;
- two local sessions remain isolated in one browser session;
- debug trace streams during generation and collapses after completion;
- markdown formatting is correct immediately after the streamed answer finishes.
