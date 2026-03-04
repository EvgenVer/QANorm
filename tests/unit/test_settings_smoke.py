from pathlib import Path

from qanorm.logging import configure_logging, get_crawler_logger, get_ingestion_logger, get_worker_logger
from qanorm.settings import load_runtime_config


def test_runtime_config_smoke_loads_defaults() -> None:
    settings = load_runtime_config(config_dir=Path("configs"))

    assert settings.env.app_env == "local"
    assert settings.app.request_timeout_seconds == 30
    assert settings.app.ocr_render_dpi == 300
    assert settings.app.ocr_low_confidence_threshold == 0.7
    assert len(settings.sources.seed_urls) == 4
    assert "взамен" in settings.statuses.active


def test_logging_config_smoke_registers_named_loggers() -> None:
    configure_logging(config_dir=Path("configs"))

    assert get_ingestion_logger().name == "qanorm.ingestion"
    assert get_crawler_logger().name == "qanorm.crawler"
    assert get_worker_logger().name == "qanorm.worker"
