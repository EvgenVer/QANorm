"""Application settings and YAML config loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs"


class EnvironmentSettings(BaseSettings):
    """Settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_prefix="QANORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    db_url: str
    raw_storage_path: Path = Path("./data/raw")
    log_level: str = "INFO"


class AppFileConfig(BaseModel):
    """Settings loaded from configs/app.yaml."""

    request_timeout_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    rate_limit_per_second: float = Field(gt=0)
    user_agent: str = Field(min_length=1)


class SourcesConfig(BaseModel):
    """Seed source configuration."""

    seed_urls: list[str] = Field(min_length=1)


class StatusesConfig(BaseModel):
    """Status normalization rules."""

    active: list[str] = Field(min_length=1)
    inactive: list[str] = Field(min_length=1)


class RuntimeConfig(BaseModel):
    """Normalized runtime configuration bundle."""

    env: EnvironmentSettings
    app: AppFileConfig
    sources: SourcesConfig
    statuses: StatusesConfig


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file_handle:
        payload = yaml.safe_load(file_handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")

    return payload


def load_environment_settings() -> EnvironmentSettings:
    """Load environment-backed settings."""

    return EnvironmentSettings()


def load_app_file_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> AppFileConfig:
    """Load and validate app.yaml."""

    payload = _load_yaml_file(config_dir / "app.yaml")
    if "app" not in payload:
        raise ValueError("configs/app.yaml must contain an 'app' section")
    return AppFileConfig.model_validate(payload["app"])


def load_sources_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> SourcesConfig:
    """Load and validate sources.yaml."""

    payload = _load_yaml_file(config_dir / "sources.yaml")
    if "sources" not in payload:
        raise ValueError("configs/sources.yaml must contain a 'sources' section")
    return SourcesConfig.model_validate(payload["sources"])


def load_statuses_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> StatusesConfig:
    """Load and validate statuses.yaml."""

    payload = _load_yaml_file(config_dir / "statuses.yaml")
    if "statuses" not in payload:
        raise ValueError("configs/statuses.yaml must contain a 'statuses' section")
    return StatusesConfig.model_validate(payload["statuses"])


def load_runtime_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> RuntimeConfig:
    """Load the full normalized runtime configuration."""

    env = load_environment_settings()
    app = load_app_file_config(config_dir=config_dir)
    sources = load_sources_config(config_dir=config_dir)
    statuses = load_statuses_config(config_dir=config_dir)
    return RuntimeConfig(env=env, app=app, sources=sources, statuses=statuses)


@lru_cache(maxsize=1)
def get_settings() -> RuntimeConfig:
    """Return cached runtime configuration."""

    return load_runtime_config()
