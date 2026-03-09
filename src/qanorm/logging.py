"""Logging configuration helpers."""

from __future__ import annotations

import logging
import logging.config
import json
from pathlib import Path
from typing import Any

import yaml

from qanorm.observability import get_correlation_context
from qanorm.settings import DEFAULT_CONFIG_DIR


def _load_logging_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> dict[str, Any]:
    config_path = config_dir / "logging.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Logging config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file_handle:
        payload = yaml.safe_load(file_handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"Logging config must contain a mapping: {config_path}")

    return payload


def configure_logging(config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
    """Load and apply logging configuration."""

    logging.config.dictConfig(_load_logging_config(config_dir=config_dir))


class StructuredJSONFormatter(logging.Formatter):
    """Minimal JSON formatter with Loki-friendly correlation fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(get_correlation_context().as_log_extra())
        for field_name in (
            "provider_name",
            "model_name",
            "operation",
            "status",
            "duration_seconds",
            "tool_name",
            "tool_scope",
        ):
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_ingestion_logger() -> logging.Logger:
    """Return the dedicated ingestion logger."""

    return logging.getLogger("qanorm.ingestion")


def get_crawler_logger() -> logging.Logger:
    """Return the dedicated crawler logger."""

    return logging.getLogger("qanorm.crawler")


def get_worker_logger() -> logging.Logger:
    """Return the dedicated worker logger."""

    return logging.getLogger("qanorm.worker")
