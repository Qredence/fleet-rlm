from __future__ import annotations

import os
from pathlib import Path

import fleet_rlm.runtime.config as config
import pytest
import yaml
from tests.unit.fixtures_env import clear_env, write_env_file


class _FakeLM:
    def __init__(self, model, api_base=None, api_key=None, max_tokens=None):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.max_tokens = max_tokens


class _FakeJSONAdapter:
    kind = "json"

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeChatAdapter:
    kind = "chat"

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_configure_planner_from_env_with_quotes(monkeypatch, tmp_path: Path):
    env_file = write_env_file(
        tmp_path,
        lines=[
            'DSPY_LM_MODEL="openai/test-model"',
            "DSPY_LM_API_KEY='sk-test'",
            "DSPY_LM_API_BASE=https://example.test",
            "DSPY_LM_MAX_TOKENS=1234",
        ],
    )

    captured = {}

    def fake_configure(*, lm, adapter=None):
        captured["lm"] = lm
        captured["adapter"] = adapter

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "configure", fake_configure)

    clear_env(
        monkeypatch,
        "DSPY_LM_MODEL",
        "DSPY_LLM_API_KEY",
        "DSPY_LM_API_KEY",
        "DSPY_LM_API_BASE",
        "DSPY_LM_MAX_TOKENS",
    )

    assert config.configure_planner_from_env(env_file=env_file) is True
    lm = captured["lm"]
    assert lm.model == "openai/test-model"
    assert lm.api_key == "sk-test"
    assert lm.api_base == "https://example.test"
    assert lm.max_tokens == 1234
    assert captured["adapter"] is None


def test_configure_planner_from_env_fallback_api_key(monkeypatch, tmp_path: Path):
    env_file = write_env_file(
        tmp_path,
        lines=["DSPY_LM_MODEL=openai/test", "DSPY_LM_API_KEY=sk-fallback"],
    )

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "configure", lambda *, lm, adapter=None: None)

    clear_env(monkeypatch, "DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "DSPY_LM_API_KEY")

    assert config.configure_planner_from_env(env_file=env_file) is True


def test_configure_planner_from_env_missing_vars(monkeypatch, tmp_path: Path):
    env_file = write_env_file(tmp_path)

    clear_env(monkeypatch, "DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "DSPY_LM_API_KEY")

    assert config.configure_planner_from_env(env_file=env_file) is False


def test_rlm_settings_defaults():
    """Test RlmSettings model with defaults."""
    from fleet_rlm.integrations.config.env import RlmSettings

    settings = RlmSettings()
    assert settings.max_depth == 2
    assert settings.max_llm_calls == 50
    assert settings.max_iters == 60
    assert settings.deep_max_iters == 60
    assert settings.enable_adaptive_iters is True
    assert settings.delegate_max_calls_per_turn == 8
    assert settings.delegate_result_truncation_chars == 8000


def test_rlm_settings_custom():
    """Test RlmSettings model with custom values."""
    from fleet_rlm.integrations.config.env import RlmSettings

    settings = RlmSettings(max_depth=3, max_llm_calls=20, max_iters=10)
    assert settings.max_depth == 3
    assert settings.max_llm_calls == 20
    assert settings.max_iters == 10


def test_app_config_includes_rlm_settings():
    """Test AppConfig includes rlm_settings field."""
    from fleet_rlm.integrations.config.env import AppConfig, RlmSettings

    config = AppConfig(
        agent={},
        interpreter={},
        memory={},
        rlm_settings=RlmSettings(max_depth=3),
    )
    assert config.rlm_settings.max_depth == 3


def test_interpreter_config_async_execute_default():
    """InterpreterConfig should default async_execute to True."""
    from fleet_rlm.integrations.config.env import InterpreterConfig

    interpreter = InterpreterConfig()
    assert interpreter.async_execute is True


def test_agent_config_guardrail_defaults():
    """AgentConfig should expose guardrail defaults with safe values."""
    from fleet_rlm.integrations.config.env import AgentConfig

    agent = AgentConfig()
    assert agent.max_iters == 60
    assert agent.temperature == 1.0
    assert agent.delegate_model is None
    assert agent.delegate_max_tokens == 64000
    assert agent.guardrail_mode == "off"
    assert agent.min_substantive_chars == 20


def test_config_model_defaults_match_hydra_yaml():
    """Pydantic defaults should mirror Hydra defaults for shared runtime keys."""
    from fleet_rlm.integrations.config.env import AgentConfig, RlmSettings

    config_path = Path("src/fleet_rlm/integrations/config/config.yaml")
    raw = yaml.safe_load(config_path.read_text())

    assert isinstance(raw, dict)
    agent_cfg = raw["agent"]
    rlm_cfg = raw["rlm_settings"]

    assert AgentConfig().max_iters == agent_cfg["max_iters"]
    assert AgentConfig().temperature == float(agent_cfg["temperature"])
    assert AgentConfig().delegate_max_tokens == int(agent_cfg["delegate_max_tokens"])
    assert RlmSettings().max_iters == rlm_cfg["max_iters"]
    assert RlmSettings().deep_max_iters == rlm_cfg["deep_max_iters"]
    assert RlmSettings().enable_adaptive_iters == bool(rlm_cfg["enable_adaptive_iters"])
    assert (
        RlmSettings().delegate_max_calls_per_turn
        == rlm_cfg["delegate_max_calls_per_turn"]
    )
    assert (
        RlmSettings().delegate_result_truncation_chars
        == rlm_cfg["delegate_result_truncation_chars"]
    )


def test_get_delegate_lm_from_env_uses_delegate_model(monkeypatch, tmp_path: Path):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_DELEGATE_LM_MODEL=openai/delegate-mini",
            "DSPY_DELEGATE_LM_API_KEY=sk-delegate",
            "DSPY_DELEGATE_LM_API_BASE=https://delegate.example",
        ],
    )

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    clear_env(
        monkeypatch,
        "DSPY_DELEGATE_LM_MODEL",
        "DSPY_DELEGATE_LM_API_KEY",
        "DSPY_DELEGATE_LM_API_BASE",
    )

    lm = config.get_delegate_lm_from_env(env_file=env_file, default_max_tokens=2048)
    assert lm is not None
    assert lm.model == "openai/delegate-mini"
    assert lm.api_key == "sk-delegate"
    assert lm.api_base == "https://delegate.example"
    assert lm.max_tokens == 2048


def test_get_runtime_module_adapter_defaults_to_json(monkeypatch, tmp_path: Path):
    env_file = write_env_file(tmp_path)

    monkeypatch.setattr(config.dspy, "JSONAdapter", _FakeJSONAdapter)
    monkeypatch.setattr(config.dspy, "ChatAdapter", _FakeChatAdapter)
    clear_env(monkeypatch, "DSPY_STRUCTURED_OUTPUT_ADAPTER")

    adapter = config.get_runtime_module_adapter(
        "grounded_answer",
        env_file=env_file,
    )

    assert isinstance(adapter, _FakeJSONAdapter)


def test_get_runtime_module_adapter_allows_chat_override(monkeypatch, tmp_path: Path):
    env_file = write_env_file(
        tmp_path,
        lines=["DSPY_STRUCTURED_OUTPUT_ADAPTER=chat"],
    )

    monkeypatch.setattr(config.dspy, "JSONAdapter", _FakeJSONAdapter)
    monkeypatch.setattr(config.dspy, "ChatAdapter", _FakeChatAdapter)
    clear_env(monkeypatch, "DSPY_STRUCTURED_OUTPUT_ADAPTER")

    adapter = config.get_runtime_module_adapter(
        "memory_action_intent",
        env_file=env_file,
    )

    assert isinstance(adapter, _FakeChatAdapter)
    assert adapter.kwargs == {"use_native_function_calling": False}


def test_get_runtime_module_adapter_can_opt_into_native_function_calling(
    monkeypatch, tmp_path: Path
):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_STRUCTURED_OUTPUT_ADAPTER=chat",
            "DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING=true",
        ],
    )

    monkeypatch.setattr(config.dspy, "JSONAdapter", _FakeJSONAdapter)
    monkeypatch.setattr(config.dspy, "ChatAdapter", _FakeChatAdapter)
    clear_env(
        monkeypatch,
        "DSPY_STRUCTURED_OUTPUT_ADAPTER",
        "DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
    )

    adapter = config.get_runtime_module_adapter(
        "grounded_answer",
        env_file=env_file,
    )

    assert isinstance(adapter, _FakeChatAdapter)
    assert adapter.kwargs == {"use_native_function_calling": True}


def test_build_dspy_context_uses_structure_sensitive_adapter(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    class _FakeContext:
        def __enter__(self):
            captured["entered"] = True
            return self

        def __exit__(self, exc_type, exc, tb):
            captured["exited"] = True
            return False

    def _fake_context(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeContext()

    monkeypatch.setattr(config.dspy, "JSONAdapter", _FakeJSONAdapter)
    monkeypatch.setattr(config.dspy, "ChatAdapter", _FakeChatAdapter)
    monkeypatch.setattr(config.dspy, "context", _fake_context)
    monkeypatch.setattr(
        config,
        "get_runtime_module_adapter",
        lambda module_name, env_file=None: _FakeJSONAdapter(),
    )

    context_manager = config.build_dspy_context(
        lm="planner",
        module_name="grounded_answer",
    )
    with context_manager:
        pass

    assert captured["kwargs"]["lm"] == "planner"
    assert isinstance(captured["kwargs"]["adapter"], _FakeJSONAdapter)
    assert captured["entered"] is True
    assert captured["exited"] is True


def test_configure_planner_from_env_passes_default_adapter(monkeypatch, tmp_path: Path):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/test-model",
            "DSPY_LM_API_KEY=sk-test",
            "DSPY_ADAPTER=json",
        ],
    )
    captured: dict[str, object] = {}

    def fake_configure(*, lm, adapter=None):
        captured["lm"] = lm
        captured["adapter"] = adapter

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "JSONAdapter", _FakeJSONAdapter)
    monkeypatch.setattr(config.dspy, "configure", fake_configure)

    clear_env(
        monkeypatch,
        "DSPY_LM_MODEL",
        "DSPY_LM_API_KEY",
        "DSPY_ADAPTER",
    )

    assert config.configure_planner_from_env(env_file=env_file) is True
    assert isinstance(captured["adapter"], _FakeJSONAdapter)
    assert captured["adapter"].kwargs == {"use_native_function_calling": False}


def test_configure_planner_from_env_can_opt_into_native_function_calling(
    monkeypatch, tmp_path: Path
):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/test-model",
            "DSPY_LM_API_KEY=sk-test",
            "DSPY_ADAPTER=chat",
            "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING=true",
        ],
    )
    captured: dict[str, object] = {}

    def fake_configure(*, lm, adapter=None):
        captured["lm"] = lm
        captured["adapter"] = adapter

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "ChatAdapter", _FakeChatAdapter)
    monkeypatch.setattr(config.dspy, "configure", fake_configure)

    clear_env(
        monkeypatch,
        "DSPY_LM_MODEL",
        "DSPY_LM_API_KEY",
        "DSPY_ADAPTER",
        "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
    )

    assert config.configure_planner_from_env(env_file=env_file) is True
    assert isinstance(captured["adapter"], _FakeChatAdapter)
    assert captured["adapter"].kwargs == {"use_native_function_calling": True}


def test_get_delegate_lm_from_env_returns_none_on_init_error(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_DELEGATE_LM_MODEL=openai/delegate-mini",
            "DSPY_DELEGATE_LM_API_KEY=sk-delegate",
        ],
    )

    def _raise_lm(*args, **kwargs):
        raise RuntimeError(f"delegate init failed for api_key={kwargs['api_key']}")

    monkeypatch.setattr(config.dspy, "LM", _raise_lm)
    clear_env(monkeypatch, "DSPY_DELEGATE_LM_MODEL", "DSPY_DELEGATE_LM_API_KEY")

    with caplog.at_level("WARNING", logger=config.logger.name):
        lm = config.get_delegate_lm_from_env(env_file=env_file)

    assert lm is None
    assert "delegate init failed" not in caplog.text
    assert "sk-delegate" not in caplog.text
    assert "Failed to initialize delegate LM (RuntimeError)" in caplog.text


def test_get_planner_lm_from_env_local_overrides_process_env(
    monkeypatch, tmp_path: Path
):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/file-model",
            "DSPY_LLM_API_KEY=sk-from-file",
            "DSPY_LM_API_BASE=https://file.example",
        ],
    )

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/process-model")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-from-process")
    monkeypatch.setenv("DSPY_LM_API_BASE", "https://process.example")

    lm = config.get_planner_lm_from_env(env_file=env_file)
    assert lm is not None
    assert lm.model == "openai/file-model"
    assert lm.api_key == "sk-from-file"
    assert lm.api_base == "https://file.example"


def test_get_planner_lm_from_env_production_keeps_process_env(
    monkeypatch, tmp_path: Path
):
    env_file = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/file-model",
            "DSPY_LLM_API_KEY=sk-from-file",
            "DSPY_LM_API_BASE=https://file.example",
        ],
    )

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/process-model")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-from-process")
    monkeypatch.setenv("DSPY_LM_API_BASE", "https://process.example")

    lm = config.get_planner_lm_from_env(env_file=env_file)
    assert lm is not None
    assert lm.model == "openai/process-model"
    assert lm.api_key == "sk-from-process"
    assert lm.api_base == "https://process.example"


def test_prepare_env_skips_mlflow_initialization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(config, "configure_posthog_analytics_from_env", lambda: None)
    monkeypatch.setenv("APP_ENV", "local")

    env_file = tmp_path / ".env"
    config._prepare_env(env_file=env_file)


def test_prepare_env_honors_explicit_fleet_env_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    env_file = write_env_file(tmp_path, lines=["MLFLOW_ENABLED=false"])

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FLEET_RLM_ENV_PATH", str(env_file))
    monkeypatch.setenv("MLFLOW_ENABLED", "true")

    config._prepare_env()

    assert os.getenv("MLFLOW_ENABLED") == "false"
