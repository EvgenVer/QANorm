# Stage 2B Streamlit Manual Acceptance Checklist

## 1. Preparation

1. Start PostgreSQL:
   - `docker start qanorm-pg16`
2. Start the UI:
   - `.\.venv\Scripts\python.exe -m streamlit run src/qanorm/stage2a/ui/app.py`
3. Open the local Streamlit URL.

Expected result:
- the page loads without traceback
- the sidebar shows local session controls
- one empty local chat session exists by default

## 2. First question quality

Steps:
1. Ask a direct reinforced-concrete question, for example:
   - `Что СП 63 говорит про защитный слой арматуры?`
2. Inspect the first answer.

Check:
- the answer is grounded in `SP 63.13330.2018`, not in `SP 52-101-2003`
- the answer is not flattened into one line
- `Evidence`, `Ограничения`, and `Debug Trace` are collapsed by default

Expected result:
- the first answer quality is at least as good as before the conversational changes
- no regression toward older legacy reinforced-concrete standards

## 3. Follow-up and document override

Steps:
1. Ask:
   - `Что по защитному слою арматуры?`
2. Then ask:
   - `Какое минимальное значение толщины защитного слоя?`
3. Then ask:
   - `А что по СП 63?`

Check:
- the second and third turns are treated as follow-ups
- the third turn keeps the technical topic and changes the preferred document family
- the agent does not reset to a brand new unrelated topic

Expected result:
- short follow-ups such as `А что по СП 63?` preserve context instead of starting over

## 4. Expand-answer turn

Steps:
1. Ask a broad question that normally produces `partial`
2. Then ask:
   - `Дай конкретную информацию при каких значениях сколько`

Check:
- the next turn performs another retrieval pass
- the evidence pack is updated instead of just repeating the old answer
- the answer becomes more specific when enough context is available

Expected result:
- a partial answer can be expanded by follow-up turns inside the same local session

## 5. New local session and reset

Steps:
1. In an active chat with messages, click `Новая сессия`
2. Verify the transcript becomes empty
3. Switch back to the previous session
4. Click `Сбросить текущую сессию` only in the active session

Check:
- the new session starts from zero
- the previous session remains available until explicitly reset
- resetting one session does not affect another local session

Expected result:
- local multi-session behavior works inside one browser session

## 6. Session isolation

Steps:
1. In session A ask a reinforced-concrete question
2. Create session B and ask a fire-safety question
3. Switch back and forth between sessions

Check:
- session A keeps only the reinforced-concrete thread
- session B keeps only the fire-safety thread
- evidence, limitations, and debug trace do not leak between sessions

Expected result:
- local sessions are isolated from each other

## 7. Streamed debug trace

Steps:
1. Ask any non-trivial question
2. Watch the assistant area while the answer is being generated

Check:
- runtime events appear before the final answer is ready
- controller reasoning is visible in the streamed trace
- after completion the trace stays available in a collapsed expander

Expected result:
- the debug trace is informative during generation, not only after the answer

## 8. Formatting

Steps:
1. Ask a question that produces a multi-paragraph answer
2. Wait until the answer finishes
3. Do not ask a second question yet

Check:
- paragraphs and line breaks are already rendered correctly
- limitations do not show raw dicts like `{'text': ...}`
- no mojibake or unreadable nested payloads appear in UI panels

Expected result:
- formatting is correct immediately after the first render

## 9. Failure signals

The scenario fails if any of the following happens:
- UI crashes with traceback
- first-answer quality regresses to older legacy documents
- a short follow-up loses the previous topic
- `А что по СП 63?` behaves like a brand new question
- raw dict payloads appear in limitations
- debug trace does not show controller reasoning
- sessions leak into each other

## 10. Minimum acceptance result

Stage 2B manual acceptance passes if:
- first-answer quality is acceptable and stable
- follow-up turns keep context
- document override follow-ups keep topic and adjust preferred family
- new/reset session controls work
- local sessions stay isolated
- streamed debug trace is visible and useful
- final formatting is correct without asking another question
