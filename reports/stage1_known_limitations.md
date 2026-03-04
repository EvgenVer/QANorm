# Stage 1 Known Limitations

- OCR depends on a system-level `tesseract` binary being installed and available in `PATH`; the Python package alone is not enough.
- OCR quality is currently estimated by a heuristic confidence score, not by a model-specific confidence API.
- The embedding pipeline uses deterministic local vectors for repeatable tests and development, not a production semantic model.
- Search quality checks are covered by application-level tests, but the integration suite still relies on in-memory doubles instead of a live PostgreSQL FTS/vector backend.
- Source parsing is implemented against the current known source layouts; upstream markup changes will require parser fixture updates.
- Stage 1 focuses on ingestion, normalization, deduplication, indexing, and refresh orchestration; higher-level user workflows and advanced relevance tuning remain outside this scope.
