from __future__ import annotations

import importlib

import pytest


def test_app_config_defaults_and_computed_lists(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    defaults = config_module.AppConfig()
    assert defaults.app_env == "local"
    assert defaults.database_required is False
    assert defaults.cors_origins_list == ["*"]
    assert defaults.serve_ui is True
    assert defaults.expose_docs is True
    assert defaults.expose_root is True
    assert defaults.agent_max_output_chars == 5000
    assert defaults.rlm_action_max_tokens == 2048

    cfg = config_module.AppConfig(
        cors_allowed_origins=" https://app.example , https://admin.example ",
    )
    assert cfg.cors_origins_list == ["https://app.example", "https://admin.example"]


def test_app_config_applies_environment_aware_defaults(clean_runtime_env, monkeypatch):
    config_module = importlib.import_module("fleet_rlm.api.config")

    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("FLEET_RLM_SERVE_UI", raising=False)
    monkeypatch.delenv("FLEET_RLM_EXPOSE_DOCS", raising=False)
    monkeypatch.delenv("FLEET_RLM_EXPOSE_ROOT", raising=False)

    staging = config_module.AppConfig(app_env="staging")
    assert staging.database_required is True
    assert staging.serve_ui is False
    assert staging.expose_docs is False
    assert staging.expose_root is False


def test_app_config_accepts_bare_model_identifier(clean_runtime_env):
    # Bare model ids (no provider prefix) are valid at this layer: custom
    # OpenAI-/Anthropic-compatible endpoints resolve them with a provider hint +
    # api_base at the LLM-profile layer (resolver.py::build_lm_kwargs_from_resolved).
    # AppConfig has no provider context, so it must not reject them.
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(agent_model="gpt-4o")
    assert cfg.agent_model == "gpt-4o"

    # A prefixed id is still accepted unchanged.
    cfg_prefixed = config_module.AppConfig(agent_model="openai/gpt-4o")
    assert cfg_prefixed.agent_model == "openai/gpt-4o"


def test_validate_startup_or_raise_requires_database_url_when_database_is_required(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        database_required=True,
        database_url=None,  # ty: ignore[unknown-argument]
    )

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_rejects_insecure_staging_configuration(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        app_env="staging",
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        auth_required=True,
        cors_allowed_origins=["*"],
    )

    with pytest.raises(ValueError, match=r"CORS_ALLOWED_ORIGINS cannot contain '\*'"):
        cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_requires_database_for_neon(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        auth_required=True,
        database_required=False,
    )

    with pytest.raises(ValueError, match="DATABASE_REQUIRED must be true"):
        cfg.validate_startup_or_raise()


def test_neon_auth_defaults_database_required(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        auth_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
    )

    assert cfg.auth_required is True


def test_validate_startup_requires_secret_encryption_key_for_hosted_neon(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        app_env="production",
        auth_required=True,
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        cors_allowed_origins=["https://preview.qredence.ai"],
    )

    with pytest.raises(ValueError, match="FLEET_SECRET_ENCRYPTION_KEY is required"):
        cfg.validate_startup_or_raise()


def test_app_config_reads_database_url_from_env(clean_runtime_env, monkeypatch):
    """AppConfig (BaseSettings) reads DATABASE_URL directly from the environment."""
    config_module = importlib.import_module("fleet_rlm.api.config")

    monkeypatch.setenv("DATABASE_URL", "postgresql://env.example.invalid/db")
    monkeypatch.setenv("DATABASE_REQUIRED", "true")

    cfg = config_module.AppConfig()

    assert cfg.database_required is True
    assert cfg.database_url == "postgresql://env.example.invalid/db"


def test_validate_startup_or_raise_accepts_valid_neon_configuration(clean_runtime_env):
    from cryptography.fernet import Fernet

    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        app_env="production",
        auth_required=True,
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        neon_tenant_claim="fleet-prod",
        secret_encryption_key=Fernet.generate_key().decode("ascii"),
        cors_allowed_origins=["https://app.example"],
    )

    cfg.validate_startup_or_raise()
