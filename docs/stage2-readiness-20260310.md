# Stage 2 Readiness Report

Date: 2026-03-10

## Scope

This report closes the automated part of redesign blocks `AU` and `AV` for Stage 2 and records the remaining manual blockers.

## Automated Results

### AU Regression Smoke

- `AU/949` document-aware retrieval on the populated Stage 1 corpus: passed.
  - Live scoped retrieval against `СП 35.13330.2011` returned `final_scope=document_scoped`, `fallback_used=False`, `primary=3`.
- `AU/950` explicit normative question with evidence check: passed.
  - Local end-to-end worker run for `СП 35.13330.2011`, `раздел 5.1` finished with `answer_mode=partial_answer`, `coverage_status=partial`, `evidence_count=6`.
- `AU/951` non-blocking freshness branch after redesign: passed.
  - Covered by `tests/integration/test_stage_av_runtime_integration.py::test_961_integration_freshness_branch_is_non_blocking`.
- `AU/952` trusted-source fallback with TTL cache after redesign: passed.
  - Covered by `tests/integration/test_stage_av_runtime_integration.py::test_962_integration_trusted_source_fallback_reuses_ttl_cache`.
- `AU/953` open-web fallback after redesign: passed.
  - Covered by `tests/integration/test_stage_al_integration.py::test_408_integration_orchestrator_defers_open_web_until_normative_and_trusted_are_insufficient`.
- `AU/954` observability and audit stack after redesign: passed.
  - `GET /metrics` returned `200`.
  - Prometheus payload still exposes `qanorm_events_total`.
  - Live API activity increased `audit_events` by `2`.

### AV Automated Acceptance

- `AV/955-959`, `AV/961-962`, `AV/965-966`, `AV/969` are covered by `tests/integration/test_stage_av_runtime_integration.py`.
- `AV/960` is covered by `tests/integration/test_stage_ah_integration.py::test_403_integration_orchestrator_handles_multi_aspect_query`.
- `AV/963` is covered by `tests/integration/test_stage_al_integration.py::test_408_integration_orchestrator_defers_open_web_until_normative_and_trusted_are_insufficient`.
- `AV/964` is covered by `tests/integration/test_stage_ai_integration.py::test_404_integration_answer_smoke_with_mixed_normative_and_external_evidence`.
- `AV/967-968` are covered by:
  - `tests/integration/test_stage_am_integration.py::test_411_integration_blocks_prompt_injection_from_user_input`
  - `tests/integration/test_stage_am_integration.py::test_412_integration_detects_prompt_injection_in_retrieved_content_and_enforces_session_isolation`

Executed automated acceptance bundle:

- `16 passed in 9.78s`

## Acceptance Metrics

These are acceptance-harness metrics, not production telemetry.

- `document_resolution_precision_acceptance = 1.00`
  - Passed explicit document-resolution checks: `2/2`
  - Sources: live scoped retrieval smoke for `СП 35.13330.2011`, `test_930_integration_prefers_document_scoped_retrieval_for_explicit_norm_hint`
- `clarify_rate_acceptance = 1.00`
  - Clarify-path checks passed: `1/1`
  - Source: `test_947_integration_clarify_path_returns_clarify_mode`
- `retrieval_quality_acceptance = 1.00`
  - Retrieval-quality checks passed: `3/3`
  - Sources: live explicit-norm smoke, `test_940_integration_retrieval_quality_prefers_explicit_normative_locator`, `test_946_integration_direct_answer_mode_uses_primary_evidence`
- `au_regression_pass_rate = 1.00`
  - Passed checks: `6/6`
- `av_automated_acceptance_pass_rate = 1.00`
  - Closed automated tasks: `15/15` for `955-969`

## Known Limitations

- `AV/970` web UI acceptance was not completed manually in this turn.
  - The repo does not contain a browser-driven acceptance harness.
- `AV/971` Telegram acceptance is blocked by missing `QANORM_TELEGRAM_BOT_TOKEN`.
- The explicit normative smoke now retrieves the correct section and evidence, but the synthesized answer still degrades to `partial_answer` instead of a clean `direct_answer`.
- Running Stage 2 through Docker still requires rebuild/restart to pick up the latest local source changes made in this turn.

## Readiness Status

Automated redesign validation is complete.

Stage 2 is not yet signed off for final readiness because `AV/970`, `AV/971`, and the final readiness confirmation task `AV/975` still require manual acceptance and, for Telegram, missing runtime credentials.
