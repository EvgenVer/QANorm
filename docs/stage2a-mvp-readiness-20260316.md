# Stage 2A MVP Readiness Report

Date: `2026-03-16`

## Final Status

`Stage 2A MVP` is ready for acceptance against the current project thresholds.

Final acceptance run:

- command family: parallel `stage2a-eval-start` / `stage2a-eval-status`
- dataset: `eval/stage2a/questions.jsonl`
- questions: `150`
- workers: `4`
- manifest: `.cache/stage2a_eval_v5/eval_manifest.json`

## Final Metrics

- `document hit@3 = 0.9267`
- `locator hit@5 = 1.00`
- `grounded answer rate = 1.00`
- `unsupported claim rate = 0.00`
- `partial answer rate = 0.0467`
- `expected mode match rate = 0.7867`
- `wrong document rate = 0.00`

## Threshold Check

- `document hit@3 >= 0.85`: passed
- `locator hit@5 >= 0.70`: passed
- `grounded answer rate >= 0.95`: passed
- `unsupported claim rate <= 0.05`: passed
- `partial answer rate <= 0.25`: passed
- `expected mode match rate >= 0.75`: passed
- `wrong document rate <= 0.10`: passed

## What Changed Since Baseline

Baseline full eval:

- `document hit@3 = 0.74`
- `locator hit@5 = 0.00`
- `grounded answer rate = 1.00`
- `unsupported claim rate = 0.00`
- `partial answer rate = 0.1933`
- `expected mode match rate = 0.66`
- `wrong document rate = 0.00`

Final delta:

- `document hit@3`: `0.74 -> 0.9267`
- `locator hit@5`: `0.00 -> 1.00`
- `partial answer rate`: `0.1933 -> 0.0467`
- `expected mode match rate`: `0.66 -> 0.7867`

Main drivers:

- retrieval ranking and family-aware document scoring were strengthened
- locator-aware retrieval was rebuilt around `retrieval_units`
- interactive mode policy was tightened
- eval scoring was corrected to prefer the latest active edition by default
- corpus defects were repaired for `ГОСТ 27751-2014` and `СП 1.13130.2020`
- placeholder `SP 1.0` was removed from candidate selection

## Remaining Known Limitations

The MVP passes thresholds, but several limitations remain and should be treated as `vNext`, not as blockers:

- some engineering questions still degrade to `partial` even when the correct family is found
- a few ambiguity cases still return `direct` where a stricter UX might prefer `clarify`
- some legacy drift remains in niche clusters, for example reinforced concrete legacy manuals and drainage/waterproofing topics
- formulas and tables are still weak when Stage 1 extraction did not preserve them as useful text
- interactive chat still has no real conversational memory; each query is processed independently

## Recommended Next Iteration

1. Improve corpus quality for legacy-heavy domains.
   Focus on reinforced concrete legacy manuals, roofing/drainage, and formula/table extraction.

2. Add conversational session memory.
   Carry a small bounded conversation summary into `ControllerAgent` and `Composer`.

3. Tighten ambiguous-query UX.
   Revisit `clarify` policy for broad queries like generic component or requirement lookups.

4. Start DSPy optimization from the accepted eval set.
   Use the fixed `150`-question dataset as the baseline for systematic prompt/program optimization.

5. Add a small historical/edition-strict eval slice.
   Keep the default policy on latest active editions, but explicitly test historical queries separately.

## Acceptance Decision

`Stage 2A MVP` is accepted as implementation-complete and eval-ready.
