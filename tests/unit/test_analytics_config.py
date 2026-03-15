from __future__ import annotations

from fleet_rlm.analytics.config import MlflowConfig, PostHogConfig
from fleet_rlm.core.config import load_posthog_settings_from_env
from tests.unit.fixtures_env import (
    apply_mlflow_env,
    apply_posthog_defaults,
    clear_env,
)


def test_posthog_config_from_env_supports_project_default_fallback(monkeypatch):
    clear_env(monkeypatch, "POSTHOG_ENABLED", "POSTHOG_API_KEY", "POSTHOG_HOST")
    apply_posthog_defaults(monkeypatch)

    cfg = PostHogConfig.from_env()

    assert cfg.api_key == "phc_default"
    assert cfg.host == "https://eu.i.posthog.com"
    assert cfg.enabled is True


def test_posthog_config_from_env_env_overrides_take_precedence(monkeypatch):
    apply_posthog_defaults(monkeypatch)
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_env")
    monkeypatch.setenv("POSTHOG_HOST", "https://us.i.posthog.com")
    monkeypatch.setenv("POSTHOG_ENABLED", "false")

    cfg = PostHogConfig.from_env()

    assert cfg.api_key == "phc_env"
    assert cfg.host == "https://us.i.posthog.com"
    assert cfg.enabled is False


def test_core_load_posthog_settings_matches_default_fallback_logic(monkeypatch):
    clear_env(monkeypatch, "POSTHOG_ENABLED", "POSTHOG_API_KEY", "POSTHOG_HOST")
    apply_posthog_defaults(monkeypatch)

    settings = load_posthog_settings_from_env()

    assert settings["api_key"] == "phc_default"
    assert settings["host"] == "https://eu.i.posthog.com"
    assert settings["enabled"] is True


def test_mlflow_config_from_env_supports_explicit_overrides(monkeypatch):
    apply_mlflow_env(monkeypatch)

    cfg = MlflowConfig.from_env()

    assert cfg.enabled is True
    assert cfg.tracking_uri == "http://127.0.0.1:6001"
    assert cfg.experiment == "fleet-rlm-test"
    assert cfg.active_model_id == "model-123"
    assert cfg.dspy_log_traces_from_compile is True
    assert cfg.dspy_log_traces_from_eval is False
    assert cfg.dspy_log_compiles is True
    assert cfg.dspy_log_evals is True
