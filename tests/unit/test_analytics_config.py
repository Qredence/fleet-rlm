from __future__ import annotations

from fleet_rlm.analytics.config import MlflowConfig, PostHogConfig
from fleet_rlm.core.config import load_posthog_settings_from_env


def test_posthog_config_from_env_supports_project_default_fallback(monkeypatch):
    monkeypatch.delenv("POSTHOG_ENABLED", raising=False)
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_API_KEY", "phc_default"
    )
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_HOST",
        "https://eu.i.posthog.com",
    )

    cfg = PostHogConfig.from_env()

    assert cfg.api_key == "phc_default"
    assert cfg.host == "https://eu.i.posthog.com"
    assert cfg.enabled is True


def test_posthog_config_from_env_env_overrides_take_precedence(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_API_KEY", "phc_default"
    )
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_env")
    monkeypatch.setenv("POSTHOG_HOST", "https://us.i.posthog.com")
    monkeypatch.setenv("POSTHOG_ENABLED", "false")

    cfg = PostHogConfig.from_env()

    assert cfg.api_key == "phc_env"
    assert cfg.host == "https://us.i.posthog.com"
    assert cfg.enabled is False


def test_core_load_posthog_settings_matches_default_fallback_logic(monkeypatch):
    monkeypatch.delenv("POSTHOG_ENABLED", raising=False)
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_API_KEY", "phc_default"
    )
    monkeypatch.setattr(
        "fleet_rlm.analytics.config.PROJECT_POSTHOG_DEFAULT_HOST",
        "https://eu.i.posthog.com",
    )

    settings = load_posthog_settings_from_env()

    assert settings["api_key"] == "phc_default"
    assert settings["host"] == "https://eu.i.posthog.com"
    assert settings["enabled"] is True


def test_mlflow_config_from_env_supports_explicit_overrides(monkeypatch):
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:6001")
    monkeypatch.setenv("MLFLOW_EXPERIMENT", "fleet-rlm-test")
    monkeypatch.setenv("MLFLOW_ACTIVE_MODEL_ID", "model-123")
    monkeypatch.setenv("MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE", "true")
    monkeypatch.setenv("MLFLOW_DSPY_LOG_TRACES_FROM_EVAL", "false")
    monkeypatch.setenv("MLFLOW_DSPY_LOG_COMPILES", "true")
    monkeypatch.setenv("MLFLOW_DSPY_LOG_EVALS", "true")

    cfg = MlflowConfig.from_env()

    assert cfg.enabled is True
    assert cfg.tracking_uri == "http://127.0.0.1:6001"
    assert cfg.experiment == "fleet-rlm-test"
    assert cfg.active_model_id == "model-123"
    assert cfg.dspy_log_traces_from_compile is True
    assert cfg.dspy_log_traces_from_eval is False
    assert cfg.dspy_log_compiles is True
    assert cfg.dspy_log_evals is True
