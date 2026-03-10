"""Application settings and YAML config loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
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
TrustedSearchMode = Literal["site_query", "source_specific", "sitemap_lookup"]
TrustedExtractStrategy = Literal["generic_article", "generic_html", "custom"]
TrustedSourceTrustTier = Literal["high", "medium", "low"]


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
    embedding_output_dimensions: int = Field(default=768, gt=0)
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
    max_message_length: int = Field(default=3500, gt=0)
    long_polling_timeout_seconds: int = Field(default=20, gt=0)
    parse_mode: str = Field(default="HTML", min_length=1)


class SearchRuntimeConfig(BaseModel):
    """Search configuration for trusted and open-web fallback."""

    open_web_provider: OpenWebProvider = "searxng"
    open_web_max_results: int = Field(default=5, gt=0)
    open_web_request_timeout_seconds: int = Field(default=15, gt=0)
    open_web_user_agent: str = Field(default="QANorm-SearXNG/1.0", min_length=1)
    trusted_domains: list[str] = Field(default_factory=list)


class QAFileConfig(BaseModel):
    """Settings loaded from configs/qa.yaml."""

    session: SessionRuntimeConfig
    providers: ProvidersRuntimeConfig
    web: WebRuntimeConfig
    telegram: TelegramRuntimeConfig
    search: SearchRuntimeConfig


class TrustedSourceDefaultsConfig(BaseModel):
    """Shared defaults for online trusted-source retrieval."""

    search_mode: TrustedSearchMode = "site_query"
    max_results: int = Field(default=5, gt=0)
    fetch_timeout_seconds: int = Field(default=15, gt=0)
    max_pages_per_query: int = Field(default=5, gt=0)
    extraction_strategy: TrustedExtractStrategy = "generic_article"
    cache_enabled: bool = True
    search_cache_ttl_hours: int = Field(default=24, gt=0)
    page_cache_ttl_hours: int = Field(default=168, gt=0)
    extraction_cache_ttl_hours: int = Field(default=168, gt=0)


class TrustedSourceSearchConfig(BaseModel):
    """Source-specific search policy for online trusted retrieval."""

    mode: TrustedSearchMode = "site_query"
    sitemap_urls: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    allowed_prefixes: list[str] = Field(default_factory=list)
    blocked_prefixes: list[str] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    max_results: int = Field(default=5, gt=0)


class TrustedSourceFetchConfig(BaseModel):
    """Fetch-time controls for trusted-source page loading."""

    timeout_seconds: int = Field(default=15, gt=0)
    max_pages_per_query: int = Field(default=5, gt=0)


class TrustedSourceExtractConfig(BaseModel):
    """Extraction policy for a trusted source."""

    strategy: TrustedExtractStrategy = "generic_article"
    article_selectors: list[str] = Field(default_factory=list)
    remove_selectors: list[str] = Field(default_factory=list)


class TrustedSourceCacheConfig(BaseModel):
    """Bounded shared cache settings for trusted-source retrieval."""

    enabled: bool = True
    search_ttl_hours: int = Field(default=24, gt=0)
    page_ttl_hours: int = Field(default=168, gt=0)
    extraction_ttl_hours: int = Field(default=168, gt=0)


class TrustedSourceAdapterConfig(BaseModel):
    """One allowlisted trusted-source registry card."""

    source_id: str | None = Field(default=None, min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    domain: str = Field(min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    language: str = Field(default="en", min_length=2)
    country: str | None = Field(default=None, min_length=2)
    trust_tier: TrustedSourceTrustTier = "high"
    search: TrustedSourceSearchConfig = Field(default_factory=TrustedSourceSearchConfig)
    fetch: TrustedSourceFetchConfig = Field(default_factory=TrustedSourceFetchConfig)
    extract: TrustedSourceExtractConfig = Field(default_factory=TrustedSourceExtractConfig)
    cache: TrustedSourceCacheConfig = Field(default_factory=TrustedSourceCacheConfig)

    # Backward-compatible fields kept until the legacy local-index implementation is removed.
    sitemap_urls: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    allowed_prefixes: list[str] = Field(default_factory=list)
    blocked_prefixes: list[str] = Field(default_factory=list)
    max_documents_per_sync: int = Field(default=100, gt=0)
    chunk_size_chars: int = Field(default=1600, gt=0)
    chunk_overlap_chars: int = Field(default=200, ge=0)

    @model_validator(mode="after")
    def _hydrate_compatibility_fields(self) -> "TrustedSourceAdapterConfig":
        """Mirror new nested config into legacy flat fields until services are refactored."""

        if self.source_id is None:
            self.source_id = self.domain.replace(".", "_").replace("-", "_")
        if self.display_name is None:
            self.display_name = self.domain
        if self.base_url is None:
            self.base_url = f"https://{self.domain}"

        if not self.search.sitemap_urls and self.sitemap_urls:
            self.search.sitemap_urls = list(self.sitemap_urls)
        if not self.sitemap_urls and self.search.sitemap_urls:
            self.sitemap_urls = list(self.search.sitemap_urls)

        if not self.search.seed_urls and self.seed_urls:
            self.search.seed_urls = list(self.seed_urls)
        if not self.seed_urls and self.search.seed_urls:
            self.seed_urls = list(self.search.seed_urls)

        if not self.search.allowed_prefixes and self.allowed_prefixes:
            self.search.allowed_prefixes = list(self.allowed_prefixes)
        if not self.allowed_prefixes and self.search.allowed_prefixes:
            self.allowed_prefixes = list(self.search.allowed_prefixes)

        if not self.search.blocked_prefixes and self.blocked_prefixes:
            self.search.blocked_prefixes = list(self.blocked_prefixes)
        if not self.blocked_prefixes and self.search.blocked_prefixes:
            self.blocked_prefixes = list(self.search.blocked_prefixes)

        if self.max_documents_per_sync == 100:
            self.max_documents_per_sync = self.fetch.max_pages_per_query

        return self


class TrustedSourcesFileConfig(BaseModel):
    """Trusted-source registry loaded from dedicated config."""

    defaults: TrustedSourceDefaultsConfig = Field(default_factory=TrustedSourceDefaultsConfig)
    sources: list[TrustedSourceAdapterConfig] = Field(default_factory=list)


class RuntimeConfig(BaseModel):
    """Normalized runtime configuration bundle."""

    env: EnvironmentSettings
    app: AppFileConfig
    sources: SourcesConfig
    statuses: StatusesConfig
    qa: QAFileConfig
    trusted_sources: TrustedSourcesFileConfig


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


def load_trusted_sources_file_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> TrustedSourcesFileConfig:
    """Load and validate trusted_sources.yaml."""

    payload = _load_yaml_file(config_dir / "trusted_sources.yaml")
    if "trusted_sources" not in payload:
        raise ValueError("configs/trusted_sources.yaml must contain a 'trusted_sources' section")
    return TrustedSourcesFileConfig.model_validate(payload["trusted_sources"])


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
    trusted_sources = load_trusted_sources_file_config(config_dir=config_dir)
    trusted_domains = sorted(
        {
            *qa.search.trusted_domains,
            *(item.domain for item in trusted_sources.sources),
        }
    )
    qa.search.trusted_domains = trusted_domains
    _validate_stage_two_environment(env=env, qa=qa)
    return RuntimeConfig(
        env=env,
        app=app,
        sources=sources,
        statuses=statuses,
        qa=qa,
        trusted_sources=trusted_sources,
    )


@lru_cache(maxsize=1)
def get_app_config() -> AppFileConfig:
    """Return cached app-file configuration."""

    return load_app_file_config()


@lru_cache(maxsize=1)
def get_qa_config() -> QAFileConfig:
    """Return cached Stage 2 file configuration."""

    return load_qa_file_config()


@lru_cache(maxsize=1)
def get_trusted_sources_config() -> TrustedSourcesFileConfig:
    """Return cached trusted-source adapter configuration."""

    return load_trusted_sources_file_config()


@lru_cache(maxsize=1)
def get_settings() -> RuntimeConfig:
    """Return cached runtime configuration."""

    return load_runtime_config()
