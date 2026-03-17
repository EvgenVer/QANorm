"""Stage 2A YAML configuration loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from qanorm.settings import DEFAULT_CONFIG_DIR, _load_yaml_file


class Stage2AProviderApiConfig(BaseModel):
    """Provider API environment wiring."""

    base_url_env: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)


class Stage2AProviderConfig(BaseModel):
    """Primary provider configuration."""

    name: str = Field(min_length=1)
    api: Stage2AProviderApiConfig


class Stage2ADspyConfig(BaseModel):
    """DSPy cache and bootstrap settings."""

    cache_enabled: bool = True
    cache_dir_env: str = Field(min_length=1)


class Stage2AModelConfig(BaseModel):
    """Configured model names for Stage 2A."""

    controller: str = Field(min_length=1)
    composer: str = Field(min_length=1)
    verifier: str = Field(min_length=1)
    reranker: str = Field(min_length=1)
    embeddings: str = Field(min_length=1)


class Stage2AIndexingConfig(BaseModel):
    """Derived indexing thresholds."""

    semantic_block_min_chars: int = Field(ge=1)
    semantic_block_target_chars: int = Field(ge=1)
    semantic_block_max_chars: int = Field(ge=1)
    semantic_block_max_nodes: int = Field(ge=1)
    document_card_max_headings: int = Field(ge=1)
    embed_batch_size: int = Field(ge=1)


class Stage2AEmbeddingsConfig(BaseModel):
    """Embedding behavior and estimation knobs."""

    query_task_type: str = Field(min_length=1)
    document_task_type: str = Field(min_length=1)
    output_dimensionality: int = Field(ge=1)
    estimated_text_input_price_per_million_tokens: float | None = Field(default=None, ge=0.0)
    average_chars_per_token: float = Field(default=4.0, gt=0.0)


class Stage2ARuntimeConfig(BaseModel):
    """Runtime limits and provider request behavior."""

    max_tool_steps: int = Field(ge=1)
    max_corrective_iterations: int = Field(ge=0)
    request_timeout_seconds: int = Field(gt=0)
    retry_attempts: int = Field(ge=0)
    retry_backoff_seconds: float = Field(gt=0.0)
    enable_streaming: bool = True
    enable_debug_trace: bool = True


class Stage2AGenerationConfig(BaseModel):
    """Generation settings for agent-facing roles."""

    controller_temperature: float = Field(ge=0.0)
    composer_temperature: float = Field(ge=0.0)
    verifier_temperature: float = Field(ge=0.0)
    max_answer_tokens: int = Field(ge=1)
    max_verifier_tokens: int = Field(ge=1)


class Stage2ARetrievalConfig(BaseModel):
    """Retrieval limits and shortlist sizes."""

    discover_documents_top_k: int = Field(ge=1)
    document_shortlist_size: int = Field(ge=1)
    lexical_top_k: int = Field(ge=1)
    dense_top_k: int = Field(ge=1)
    merged_top_k: int = Field(ge=1)
    rerank_top_k: int = Field(ge=1)
    evidence_pack_size: int = Field(ge=1)
    neighbor_window: int = Field(ge=0)
    min_direct_answer_evidence: int = Field(ge=1)
    enable_partial_answer_on_low_confidence: bool = True


class Stage2AUiConfig(BaseModel):
    """Minimal Streamlit UI settings for MVP."""

    title: str = Field(min_length=1)
    stream: bool = True
    show_debug_panel: bool = True
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class Stage2AConversationConfig(BaseModel):
    """Bounded in-session conversation memory settings for Stage 2B."""

    max_messages: int = Field(ge=1)
    max_summary_chars: int = Field(ge=1)
    max_document_hints: int = Field(ge=1)
    max_locator_hints: int = Field(ge=1)
    max_open_threads: int = Field(ge=1)
    max_runtime_events: int = Field(ge=1)
    max_session_title_chars: int = Field(ge=1)


class Stage2AConfig(BaseModel):
    """Normalized Stage 2A configuration bundle."""

    provider: Stage2AProviderConfig
    dspy: Stage2ADspyConfig
    models: Stage2AModelConfig
    indexing: Stage2AIndexingConfig
    embeddings: Stage2AEmbeddingsConfig
    runtime: Stage2ARuntimeConfig
    generation: Stage2AGenerationConfig
    retrieval: Stage2ARetrievalConfig
    ui: Stage2AUiConfig
    conversation: Stage2AConversationConfig


def load_stage2a_config(config_path: Path | None = None) -> Stage2AConfig:
    """Load and validate the Stage 2A YAML config."""

    configured_path = os.environ.get("QANORM_STAGE2A_CONFIG_PATH")
    path = Path(configured_path) if configured_path else (config_path or DEFAULT_CONFIG_DIR / "stage2a.yaml")
    payload = _load_yaml_file(path)
    if "stage2a" not in payload:
        raise ValueError(f"{path} must contain a 'stage2a' section")
    return Stage2AConfig.model_validate(payload["stage2a"])


@lru_cache(maxsize=1)
def get_stage2a_config() -> Stage2AConfig:
    """Return cached Stage 2A configuration."""

    return load_stage2a_config()
