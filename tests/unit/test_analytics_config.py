from __future__ import annotations

from fleet_rlm.analytics.config import PostHogConfig
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
