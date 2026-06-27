from __future__ import annotations

from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any

import pytest


class _FakeAdapter:
    def __init__(self, kind: str, *, use_native_function_calling: bool = False) -> None:
        self.kind = kind
        self.use_native_function_calling = use_native_function_calling


class _FakeLM:
    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs


class _FakeContext(AbstractContextManager[dict[str, Any]]):
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeDSPy:
    def __init__(self) -> None:
        self.configure_calls: list[dict[str, Any]] = []
        self.configure_cache_calls: list[dict[str, Any]] = []
        self.context_calls: list[dict[str, Any]] = []

    def JSONAdapter(self, *, use_native_function_calling: bool = False) -> _FakeAdapter:
        return _FakeAdapter("json", use_native_function_calling=use_native_function_calling)

    def ChatAdapter(self, *, use_native_function_calling: bool = False) -> _FakeAdapter:
        return _FakeAdapter("chat", use_native_function_calling=use_native_function_calling)

    def LM(self, model: str, **kwargs: Any) -> _FakeLM:
        return _FakeLM(model, **kwargs)

    def context(self, **kwargs: Any) -> _FakeContext:
        self.context_calls.append(kwargs)
        return _FakeContext(kwargs)

    def configure(self, **kwargs: Any) -> None:
        self.configure_calls.append(kwargs)

    def configure_cache(self, **kwargs: Any) -> None:
        self.configure_cache_calls.append(kwargs)


def _patch_runtime_config(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _FakeDSPy]:
    from fleet_rlm.runtime import config as runtime_config

    fake_dspy = _FakeDSPy()
    monkeypatch.setattr(runtime_config, "_prepare_env", lambda **_: None)
    monkeypatch.setattr(runtime_config, "_import_dspy", lambda: fake_dspy)
    return runtime_config, fake_dspy


def test_get_default_dspy_adapter_from_env_uses_env_flags(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, _ = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_ADAPTER", "json")
    clean_runtime_env.setenv("DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING", "true")

    adapter = runtime_config.get_default_dspy_adapter_from_env()

    assert adapter.kind == "json"
    assert adapter.use_native_function_calling is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("chat", "chat"), ("JSON", "json"), ("off", None), (" auto ", None), (None, None)],
)
def test_normalize_adapter_name_handles_supported_inputs(value: str | None, expected: str | None) -> None:
    from fleet_rlm.runtime import config as runtime_config

    assert runtime_config._normalize_adapter_name(value) == expected


def test_normalize_adapter_name_rejects_unknown_values() -> None:
    from fleet_rlm.runtime import config as runtime_config

    with pytest.raises(ValueError, match="Unsupported DSPy adapter name"):
        runtime_config._normalize_adapter_name("xml")


def test_get_runtime_module_adapter_only_applies_to_structure_sensitive_modules(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, _ = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_STRUCTURED_OUTPUT_ADAPTER", "chat")
    clean_runtime_env.setenv("DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING", "true")

    adapter = runtime_config.get_runtime_module_adapter("grounded_answer")
    missing = runtime_config.get_runtime_module_adapter("plan_code_change")

    assert adapter.kind == "chat"
    assert adapter.use_native_function_calling is True
    assert missing is None


def test_build_dspy_context_uses_resolved_adapter_and_supports_empty_context(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_ADAPTER", "chat")

    lm = SimpleNamespace(name="planner")
    ctx = runtime_config.build_dspy_context(lm=lm, allow_tool_async_sync_conversion=True)

    assert len(fake_dspy.context_calls) == 1
    assert fake_dspy.context_calls[0]["lm"] is lm
    assert fake_dspy.context_calls[0]["allow_tool_async_sync_conversion"] is True
    assert fake_dspy.context_calls[0]["adapter"].kind == "chat"
    assert fake_dspy.configure_cache_calls[0] == {
        "enable_disk_cache": False,
        "enable_memory_cache": True,
        "restrict_pickle": True,
    }
    assert isinstance(ctx, _FakeContext)

    clean_runtime_env.delenv("DSPY_ADAPTER", raising=False)
    empty = runtime_config.build_dspy_context()
    with empty as value:
        assert value is None


def test_configure_planner_from_env_builds_lm_and_configures_dspy(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_LM_MODEL", "openai/gpt-4.1")
    clean_runtime_env.setenv("DSPY_LLM_API_KEY", "planner-key")
    clean_runtime_env.setenv("DSPY_LM_API_BASE", "https://api.example.test")
    clean_runtime_env.setenv("DSPY_LM_MAX_TOKENS", "777")

    configured = runtime_config.configure_planner_from_env()

    assert configured is True
    assert fake_dspy.configure_cache_calls[0]["enable_disk_cache"] is False
    lm = fake_dspy.configure_calls[0]["lm"]
    assert lm.model == "openai/gpt-4.1"
    # ResponseAPILM inherits from BaseLM which always includes temperature in kwargs
    assert lm.kwargs == {
        "api_base": "https://api.example.test",
        "api_key": "planner-key",
        "max_tokens": 777,
        "temperature": None,
    }


def test_configure_dspy_cache_security_allows_explicit_restricted_disk_cache(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("FLEET_RLM_ENABLE_DSPY_DISK_CACHE", "true")

    runtime_config.configure_dspy_cache_security()

    assert fake_dspy.configure_cache_calls == [
        {
            "enable_disk_cache": True,
            "enable_memory_cache": True,
            "restrict_pickle": True,
        }
    ]


def test_get_planner_and_delegate_lm_from_env_use_expected_fallbacks(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, _ = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_LM_MODEL", "planner-model")
    clean_runtime_env.setenv("DSPY_LM_API_KEY", "planner-key")
    clean_runtime_env.setenv("DSPY_LM_API_BASE", "https://planner.example.test")
    clean_runtime_env.setenv("DSPY_LM_MAX_TOKENS", "321")
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_MODEL", "delegate-model")

    planner = runtime_config.get_planner_lm_from_env(model_name="override-model")
    delegate = runtime_config.get_delegate_lm_from_env(default_max_tokens=123)

    assert planner.model == "override-model"
    assert planner.kwargs["api_key"] == "planner-key"
    assert delegate.model == "delegate-model"
    assert delegate.kwargs == {
        "api_base": "https://planner.example.test",
        "api_key": "planner-key",
        "max_tokens": 123,
    }


def test_get_delegate_lm_from_env_returns_none_on_init_failure(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_MODEL", "delegate-model")
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_API_KEY", "delegate-key")
    monkeypatch.setattr(runtime_config, "_build_lm", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))

    assert runtime_config.get_delegate_lm_from_env() is None
    assert fake_dspy.configure_cache_calls[0]["enable_disk_cache"] is False


def test_build_lm_does_not_force_openai_provider_without_opt_in(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: bare model + api_base must NOT auto-inject openai provider."""
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_LM_MODEL", "claude-sonnet-4")
    clean_runtime_env.setenv("DSPY_LLM_API_KEY", "anthropic-key")
    clean_runtime_env.setenv("DSPY_LM_API_BASE", "https://api.anthropic.com")

    lm = runtime_config.get_planner_lm_from_env()

    assert lm is not None
    assert lm.model == "claude-sonnet-4"
    assert "custom_llm_provider" not in lm.kwargs


def test_build_lm_uses_explicit_custom_provider_hint(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting DSPY_LM_CUSTOM_PROVIDER=openai should forward the hint."""
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_LM_MODEL", "gemini-3-flash")
    clean_runtime_env.setenv("DSPY_LLM_API_KEY", "key")
    clean_runtime_env.setenv("DSPY_LM_API_BASE", "https://proxy.example.test/v1")
    clean_runtime_env.setenv("DSPY_LM_CUSTOM_PROVIDER", "openai")

    lm = runtime_config.get_planner_lm_from_env()

    assert lm is not None
    assert lm.kwargs.get("custom_llm_provider") == "openai"


def test_delegate_build_lm_uses_delegate_custom_provider(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate LM should use its own DSPY_DELEGATE_LM_CUSTOM_PROVIDER."""
    runtime_config, fake_dspy = _patch_runtime_config(monkeypatch)
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_MODEL", "claude-haiku")
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_API_KEY", "key")
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_API_BASE", "https://api.anthropic.com")
    clean_runtime_env.setenv("DSPY_DELEGATE_LM_CUSTOM_PROVIDER", "anthropic")

    lm = runtime_config.get_delegate_lm_from_env()

    assert lm is not None
    assert lm.kwargs.get("custom_llm_provider") == "anthropic"


def test_load_posthog_settings_from_env_respects_defaults_and_bounds(
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.runtime import config as runtime_config

    clean_runtime_env.setenv("POSTHOG_API_KEY", "phk_test")
    clean_runtime_env.setenv("POSTHOG_ENABLED", "true")
    clean_runtime_env.setenv("POSTHOG_HOST", "https://app.posthog.test")
    clean_runtime_env.setenv("POSTHOG_FLUSH_INTERVAL", "1.5")
    clean_runtime_env.setenv("POSTHOG_FLUSH_AT", "0")
    clean_runtime_env.setenv("POSTHOG_ENABLE_DSPY_OPTIMIZATION", "true")
    clean_runtime_env.setenv("POSTHOG_INPUT_TRUNCATION", "0")
    clean_runtime_env.setenv("POSTHOG_OUTPUT_TRUNCATION", "-10")
    clean_runtime_env.setenv("POSTHOG_REDACT_SENSITIVE", "false")
    clean_runtime_env.setenv("POSTHOG_DISTINCT_ID", "worker-1")

    settings = runtime_config.load_posthog_settings_from_env()

    assert settings == {
        "enabled": True,
        "api_key": "phk_test",
        "host": "https://app.posthog.test",
        "flush_interval": 1.5,
        "flush_at": 1,
        "enable_dspy_optimization": True,
        "input_truncation_chars": 1,
        "output_truncation_chars": 1,
        "redact_sensitive": False,
        "distinct_id": "worker-1",
    }
