from pathlib import Path

from qanorm.stage2a.config import load_stage2a_config


def test_stage2a_config_loads_all_runtime_sections() -> None:
    config = load_stage2a_config(Path("configs/stage2a.yaml"))

    assert config.provider.name == "gemini"
    assert config.dspy.cache_enabled is True
    assert config.runtime.max_tool_steps == 5
    assert config.runtime.retry_attempts == 4
    assert config.generation.controller_temperature == 0.1
    assert config.retrieval.evidence_pack_size == 8
    assert config.ui.port == 8501
    assert config.conversation.max_messages == 8
    assert config.conversation.max_session_title_chars == 80
