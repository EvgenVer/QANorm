# Stage 2A Eval Report

Date: `2026-03-16`

Source run:

- command: `.\.venv\Scripts\python.exe -m qanorm.cli.main stage2a-eval --questions-path eval/stage2a/questions.jsonl`
- questions: `150`
- raw json report: `.cache/stage2a-eval-20260316.json`

## Target Thresholds

- `document hit@3 >= 0.85`
- `locator hit@5 >= 0.70`
- `grounded answer rate >= 0.95`
- `unsupported claim rate <= 0.05`
- `partial answer rate <= 0.25`
- `expected mode match rate >= 0.75`
- `wrong document rate <= 0.10`

## Actual Metrics

- `document hit@3 = 0.74`
- `locator hit@5 = 0.00`
- `grounded answer rate = 1.00`
- `unsupported claim rate = 0.00`
- `partial answer rate = 0.1933`
- `expected mode match rate = 0.66`
- `wrong document rate = 0.00`

## Threshold Status

- `document hit@3`: failed
- `locator hit@5`: failed
- `grounded answer rate`: passed
- `unsupported claim rate`: passed
- `partial answer rate`: passed
- `expected mode match rate`: failed
- `wrong document rate`: passed

## Scenario Breakdown

- `no_explicit_norm_engineering` (`82`)
  - `document hit@3 = 0.7439`
  - `grounded answer rate = 1.00`
  - `partial answer rate = 0.1463`
  - `mode match rate = 0.6951`
- `explicit_document_without_locator` (`33`)
  - `document hit@3 = 0.6970`
  - `grounded answer rate = 1.00`
  - `partial answer rate = 0.2424`
  - `mode match rate = 0.7576`
- `compact_alias_dirty_input` (`15`)
  - `document hit@3 = 0.6667`
  - `grounded answer rate = 1.00`
  - `partial answer rate = 0.2667`
  - `mode match rate = 0.7333`
- `ambiguous_scenario` (`10`)
  - `document hit@3 = 1.00`
  - `grounded answer rate = 1.00`
  - `partial answer rate = 0.10`
  - `mode match rate = 0.00`
- `diagnostic_locator_hidden` (`10`)
  - `document hit@3 = 0.70`
  - `locator hit@5 = 0.00`
  - `grounded answer rate = 1.00`
  - `partial answer rate = 0.40`
  - `mode match rate = 0.60`

## Main Failure Clusters

1. Document resolution still misses too many expected documents.
   - total document misses: `39 / 150`
   - most affected scenarios:
   - `no_explicit_norm_engineering`: `21`
   - `explicit_document_without_locator`: `10`
   - `compact_alias_dirty_input`: `5`
   - `diagnostic_locator_hidden`: `3`

2. Locator retrieval is not acceptable yet.
   - `locator hit@5 = 0.00`
   - the hidden diagnostic slice confirms that locator-aware retrieval still does not reliably return the expected locator in the answer evidence pack

3. The system is too eager to answer instead of clarifying.
   - `ambiguous_scenario mode match rate = 0.00`
   - the runtime tends to produce `direct` answers where the eval set expects `clarify`

4. Compact aliases and short dirty input are still weak.
   - `compact_alias_dirty_input document hit@3 = 0.6667`
   - alias handling improved, but still does not consistently anchor the right document

5. Document edition/version drift shows up in thermal and fire topics.
   - multiple misses come from answers grounded in a different edition than the current expected document
   - notable clusters:
   - `СП 50.13330.2012` vs newer `СП 50.13330.2024`
   - fire safety queries drifting to adjacent or legacy documents instead of the expected `СП 1.13130.2020` / `СП 2.13130.2020` / `ФЗ-123`

6. Some direct questions still degrade to partial despite finding the correct document.
   - total partial answers: `29 / 150`
   - this is within the target threshold overall, but it still hurts useful-answer rate in `explicit_document_without_locator` and `diagnostic_locator_hidden`

## Priority Fixes

1. Improve document resolution and edition ranking.
   - especially for thermal, fire, and reliability queries

2. Rework locator-aware retrieval.
   - hidden diagnostic slice is currently failing completely

3. Tighten clarify policy for ambiguous questions.
   - the controller should stop pretending broad questions are direct-answerable

4. Strengthen compact alias normalization.
   - especially `ГОСТ27751`, `СП50`, `СП1`, `СП17`

5. Reduce unnecessary `partial` downgrade when the correct document is already found.

## Current Readiness

`Stage 2A MVP` is not ready for acceptance against the current target thresholds.

Blocking gaps:

- `document hit@3` below threshold
- `locator hit@5` far below threshold
- `expected mode match rate` below threshold
