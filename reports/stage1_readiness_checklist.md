# Stage 1 Readiness Checklist

- Run `qanorm ingestion-metrics` and confirm the aggregated snapshot is generated successfully.
- Run `qanorm ingestion-report` and confirm the report includes metrics, target comparison, and readiness checks.
- Verify the numeric targets from `Plan.md` remain satisfied in the current dataset snapshot.
- Verify no `inactive` documents appear in the active index.
- Verify every `active` document has a linked active version.
- Verify every active version has either extracted text or OCR output.
- Re-run the limited-sample end-to-end ingestion tests before release or handoff.
- Re-run the full automated test suite before release or handoff.
