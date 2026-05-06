from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona.payload_models import (
    ContextSource,
    SandboxLmRuntimeConfig,
    normalized_context_sources,
)


def test_sandbox_lm_runtime_config_from_raw_normalizes_values() -> None:
    config = SandboxLmRuntimeConfig.from_raw(
        {
            "model": "  openai/gpt-4.1  ",
            "api_key": "  secret-key  ",
            "api_base": "  https://litellm.example  ",
            "max_tokens": "8192",
            "delegate_model": "  openai/gpt-4.1-mini  ",
            "delegate_api_key": "  delegate-key  ",
            "delegate_api_base": "  https://delegate.example  ",
        }
    )

    assert config.model == "openai/gpt-4.1"
    assert config.api_key == "secret-key"
    assert config.api_base == "https://litellm.example"
    assert config.max_tokens == 8192
    assert config.delegate_model == "openai/gpt-4.1-mini"
    assert config.delegate_api_key == "delegate-key"
    assert config.delegate_api_base == "https://delegate.example"


def test_sandbox_lm_runtime_config_invalid_max_tokens_falls_back_to_default() -> None:
    config = SandboxLmRuntimeConfig.from_raw(
        {
            "model": "openai/gpt-4.1",
            "api_key": "secret-key",
            "max_tokens": "invalid",
        }
    )

    assert config.max_tokens == 64_000


def test_sandbox_lm_runtime_config_requires_dict() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        SandboxLmRuntimeConfig.from_raw("invalid")


def test_context_source_from_raw_normalizes_fields() -> None:
    source = ContextSource.from_raw(
        {
            "source_id": "  ctx-1  ",
            "kind": "  file  ",
            "host_path": "  /tmp/input.txt  ",
            "staged_path": "  /workspace/input.txt  ",
            "source_type": "  markdown  ",
            "extraction_method": "  copy  ",
            "file_count": "3",
            "skipped_count": "2",
            "warnings": ["  first  ", "", None, " second "],
        }
    )

    assert source.source_id == "ctx-1"
    assert source.kind == "file"
    assert source.host_path == "/tmp/input.txt"
    assert source.staged_path == "/workspace/input.txt"
    assert source.source_type == "markdown"
    assert source.extraction_method == "copy"
    assert source.file_count == 3
    assert source.skipped_count == 2
    assert source.warnings == ["first", "second"]


def test_normalized_context_sources_skips_invalid_entries() -> None:
    sources = normalized_context_sources(
        [
            {
                "source_id": "ctx-1",
                "kind": "file",
                "host_path": "/tmp/a.txt",
                "staged_path": "/workspace/a.txt",
            },
            {"invalid": True},
            "not-a-dict",
        ]
    )

    assert len(sources) == 1
    assert sources[0].source_id == "ctx-1"
