from __future__ import annotations

import importlib
import os


def test_build_server_state_creates_ready_compatible_state(clean_runtime_env):
    bootstrap_module = importlib.import_module("fleet_rlm.api.bootstrap")
    config_module = importlib.import_module("fleet_rlm.api.config")
    auth_module = importlib.import_module("fleet_rlm.api.auth")

    cfg = config_module.ServerRuntimeConfig(
        app_env="local",
        database_required=False,
        auth_mode="dev",
        ws_execution_max_queue=8,
        ws_execution_drop_policy="drop_newest",
    )

    state = bootstrap_module.build_server_state(cfg)

    assert state.config_deps.config is cfg
    assert isinstance(state.auth_deps.auth_provider, auth_module.DevAuthProvider)
    assert state.session_cache_deps.sessions == {}
    assert state.diagnostics_deps.optional_service_status["planner_lm"] == "pending"
    assert state.is_ready is False

    state.lm_deps.planner_lm = object()
    assert state.is_ready is True


def test_prime_runtime_env_loads_env_file_in_local_mode(clean_runtime_env, tmp_path, monkeypatch):
    bootstrap_module = importlib.import_module("fleet_rlm.api.bootstrap")
    config_module = importlib.import_module("fleet_rlm.api.config")

    env_path = tmp_path / ".env"
    env_path.write_text("FEATURE_FLAG=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FEATURE_FLAG", "existing")

    cfg = config_module.ServerRuntimeConfig(app_env="local", env_path=env_path)
    bootstrap_module.prime_runtime_env(cfg)

    assert os.getenv("FEATURE_FLAG") == "from-file"


def test_get_planner_lm_from_env_shim_unpacks_four_helpers(monkeypatch):
    bootstrap_module = importlib.import_module("fleet_rlm.api.bootstrap")
    sentinel = object()

    monkeypatch.setattr(
        bootstrap_module,
        "_runtime_config_helpers",
        lambda: (lambda: None, lambda **_kwargs: sentinel, lambda **_kwargs: None, lambda **_kwargs: None),
    )

    assert bootstrap_module.get_planner_lm_from_env() is sentinel


def test_sync_llm_model_config_from_env_overrides_stale_hydra_defaults(clean_runtime_env, monkeypatch):
    bootstrap_module = importlib.import_module("fleet_rlm.api.bootstrap")
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        agent_model="openai/gemini/gemini-flash-lite-latest",
        agent_delegate_model=None,
        agent_delegate_small_model=None,
    )
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gemini-3.1-pro-preview")
    monkeypatch.setenv("DSPY_DELEGATE_LM_MODEL", "openai/gemini-3.5-flash")
    monkeypatch.setenv("DSPY_DELEGATE_LM_SMALL_MODEL", "openai/gemini-3.1-flash-lite")

    bootstrap_module._sync_llm_model_config_from_env(cfg)

    assert cfg.agent_model == "openai/gemini-3.1-pro-preview"
    assert cfg.agent_delegate_model == "openai/gemini-3.5-flash"
    assert cfg.agent_delegate_small_model == "openai/gemini-3.1-flash-lite"


def test_prime_runtime_env_preserves_existing_values_outside_local(clean_runtime_env, tmp_path, monkeypatch):
    bootstrap_module = importlib.import_module("fleet_rlm.api.bootstrap")
    config_module = importlib.import_module("fleet_rlm.api.config")

    env_path = tmp_path / ".env"
    env_path.write_text("FEATURE_FLAG=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FEATURE_FLAG", "existing")

    cfg = config_module.ServerRuntimeConfig(app_env="staging", env_path=env_path)
    bootstrap_module.prime_runtime_env(cfg)

    assert os.getenv("FEATURE_FLAG") == "existing"
