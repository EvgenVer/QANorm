# Тестирование Stage 1

В репозитории оставлены только тесты Stage 1:

- parser/crawler tests;
- raw storage tests;
- OCR/text extraction tests;
- repository tests;
- indexing tests;
- worker/integration tests;
- ingestion metrics tests.

Полный прогон:

```powershell
pytest tests/unit tests/integration -q
```

Если нужны только smoke-проверки ядра Stage 1, достаточно:

```powershell
pytest tests/unit/test_settings_smoke.py tests/unit/test_indexer.py tests/integration/test_stage_w_integration.py -q
```
