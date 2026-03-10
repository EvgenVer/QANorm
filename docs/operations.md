# Операции Stage 1

Инициализация БД:

```powershell
qanorm init-db
```

Проверка конфигурации:

```powershell
qanorm check-config
qanorm health-check
```

Запуск ingestion:

```powershell
qanorm crawl-seeds
qanorm run-worker
```

Переиндексация:

```powershell
qanorm reindex
qanorm reindex --document-code "SP 20.13330.2016"
```

Обновление документа:

```powershell
qanorm refresh-document "SP 20.13330.2016"
qanorm update-document "SP 20.13330.2016"
```

Контроль качества:

```powershell
qanorm ingestion-metrics
qanorm ingestion-report
```
