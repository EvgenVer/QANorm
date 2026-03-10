# Stage 2 Freshness Refresh Smoke Report

Date: 2026-03-10

## Scope

This smoke run verifies that the Stage 2 freshness branch can queue a Stage 1 refresh job for a real document from the populated corpus.

## Result

- Document code: `SP 35.13330.2011`
- Local edition label: `revision dated 2020-12-29`
- Freshness check status after queueing: `refresh_in_progress`
- Evidence freshness status: `refresh_in_progress`
- Refresh job id: `6f083204-5495-45b5-bb8d-a244d8843a56`
- Refresh job type: `refresh_document`
- Refresh job status: `pending`
- Database side effects: none committed; the smoke run was rolled back after verification.
