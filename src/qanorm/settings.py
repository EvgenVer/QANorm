"""Application settings and YAML config loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
    redis_url: str = "redis://localhost:6379/0"
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    searxng_base_url: str = "http://localhost:8080"
    telegram_bot_token: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    qwen_api_key: str | None = None
    deepseek_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    lmstudio_base_url: str = "http://localhost:1234/v1"
    vllm_base_url: str = "http://localhost:8001/v1"


class AppFileConfig(BaseModel):
    """Settings loaded from configs/app.yaml."""

    request_timeout_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    rate_limit_per_second: float = Field(gt=0)
    user_agent: str = Field(min_length=1)
    ocr_render_dpi: int = Field(default=300, ge=72)
    ocr_low_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class SourcesConfig(BaseModel):
    """Seed source configuration."""

    seed_urls: list[str] = Field(min_length=1)


class StatusesConfig(BaseModel):
    """Status normalization rules."""

    active: list[str] = Field(min_length=1)
    inactive: list[str] = Field(min_length=1)


ProviderName = Literal["gemini", "openai", "anthropic", "qwen", "deepseek", "ollama", "lmstudio", "vllm"]
StreamTransport = Literal["sse", "websocket"]
OpenWebProvider = Literal["searxng"]


class ProviderSelection(BaseModel):
    """Selected provider/model pair for one runtime role."""

    provider: ProviderName
    model: str = Field(min_length=1)


class SessionRuntimeConfig(BaseModel):
    """Session-scoped runtime limits for the assistant."""

    ttl_hours: int = Field(default=24, gt=0)
    summary_trigger_messages: int = Field(default=12, gt=0)
    summary_keep_recent_messages: int = Field(default=8, gt=0)
    max_parallel_queries_per_session: int = Field(default=1, gt=0)


class ProvidersRuntimeConfig(BaseModel):
    """Stage 2 provider selection and prompt catalog paths."""

    orchestration: ProviderSelection
    synthesis: ProviderSelection
    embeddings: ProviderSelection
    prompt_catalog_dir: Path
    prompt_default_version: str = Field(default="v1", min_length=1)
    prompt_versions: dict[str, str] = Field(default_factory=dict)


class WebRuntimeConfig(BaseModel):
    """Web-channel runtime settings."""

    stream_transport: StreamTransport = "sse"
    session_cookie_name: str = Field(min_length=1)


class TelegramRuntimeConfig(BaseModel):
    """Telegram channel settings."""

    enabled: bool = False
    use_webhook: bool = False


class SearchRuntimeConfig(BaseModel):
    """Search configuration for trusted and open-web fallback."""

    open_web_provider: OpenWebProvider = "searxng"
    open_web_max_results: int = Field(default=5, gt=0)
    trusted_domains: list[str] = Field(default_factory=list)


class QAFileConfig(BaseModel):
    """Settings loaded from configs/qa.yaml."""

    session: SessionRuntimeConfig
    providers: ProvidersRuntimeConfig
    web: WebRuntimeConfig
    telegram: TelegramRuntimeConfig
    search: SearchRuntimeConfig


class RuntimeConfig(BaseModel):
    """Normalized runtime configuration bundle."""

    env: EnvironmentSettings
    app: AppFileConfig
    sources: SourcesConfig
    statuses: StatusesConfig
    qa: QAFileConfig


REMOTE_PROVIDER_ENV_FIELDS: dict[str, str] = {
    "gemini": "gemini_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "qwen": "qwen_api_key",
    "deepseek": "deepseek_api_key",
}

LOCAL_PROVIDER_ENV_FIELDS: dict[str, str] = {
    "ollama": "ollama_base_url",
    "lmstudio": "lmstudio_base_url",
    "vllm": "vllm_base_url",
}


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


def load_qa_file_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> QAFileConfig:
    """Load and validate qa.yaml."""

    payload = _load_yaml_file(config_dir / "qa.yaml")
    if "qa" not in payload:
        raise ValueError("configs/qa.yaml must contain a 'qa' section")
    return QAFileConfig.model_validate(payload["qa"])


def _validate_stage_two_environment(env: EnvironmentSettings, qa: QAFileConfig) -> None:
    """Validate Stage 2 settings that depend on both YAML and environment values."""

    required_values = {
        "redis_url": env.redis_url,
        "api_public_url": env.api_public_url,
        "web_public_url": env.web_public_url,
    }
    missing_values = [name for name, value in required_values.items() if not value]
    if missing_values:
        joined = ", ".join(sorted(missing_values))
        raise ValueError(f"Missing required Stage 2 environment settings: {joined}")

    if qa.telegram.enabled and not env.telegram_bot_token:
        raise ValueError("QANORM_TELEGRAM_BOT_TOKEN is required when Telegram is enabled")

    if qa.search.open_web_provider == "searxng" and not env.searxng_base_url:
        raise ValueError("QANORM_SEARXNG_BASE_URL is required when SearXNG search is enabled")

    selected_providers = {
        qa.providers.orchestration.provider,
        qa.providers.synthesis.provider,
        qa.providers.embeddings.provider,
    }
    missing_provider_settings: list[str] = []
    for provider_name in selected_providers:
        if provider_name in REMOTE_PROVIDER_ENV_FIELDS:
            env_field = REMOTE_PROVIDER_ENV_FIELDS[provider_name]
            if not getattr(env, env_field):
                missing_provider_settings.append(env_field)
        elif provider_name in LOCAL_PROVIDER_ENV_FIELDS:
            env_field = LOCAL_PROVIDER_ENV_FIELDS[provider_name]
            if not getattr(env, env_field):
                missing_provider_settings.append(env_field)
        else:
            raise ValueError(f"Unsupported provider configured in qa.yaml: {provider_name}")

    if missing_provider_settings:
        joined = ", ".join(sorted(missing_provider_settings))
        raise ValueError(f"Missing provider settings for Stage 2: {joined}")


def load_runtime_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> RuntimeConfig:
    """Load the full normalized runtime configuration."""

    env = load_environment_settings()
    app = load_app_file_config(config_dir=config_dir)
    sources = load_sources_config(config_dir=config_dir)
    statuses = load_statuses_config(config_dir=config_dir)
    qa = load_qa_file_config(config_dir=config_dir)
    _validate_stage_two_environment(env=env, qa=qa)
    return RuntimeConfig(env=env, app=app, sources=sources, statuses=statuses, qa=qa)


@lru_cache(maxsize=1)
def get_app_config() -> AppFileConfig:
    """Return cached app-file configuration."""

    return load_app_file_config()


@lru_cache(maxsize=1)
def get_qa_config() -> QAFileConfig:
    """Return cached Stage 2 file configuration."""

    return load_qa_file_config()


@lru_cache(maxsize=1)
def get_settings() -> RuntimeConfig:
    """Return cached runtime configuration."""

    return load_runtime_config()
