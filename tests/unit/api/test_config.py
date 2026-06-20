from __future__ import annotations

import importlib

import pytest


def test_server_runtime_config_defaults_and_computed_lists(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    defaults = config_module.ServerRuntimeConfig()
    assert defaults.app_env == "local"
    assert defaults.database_required is False
    assert defaults.allow_debug_auth is True
    assert defaults.allow_query_auth_tokens is True
    assert defaults.cors_origins_list == ["*"]
    assert defaults.serve_ui is True
    assert defaults.expose_docs is True
    assert defaults.expose_root is True
    assert defaults.agent_max_output_chars == 5000
    assert defaults.rlm_action_max_tokens == 4096

    cfg = config_module.ServerRuntimeConfig(
        cors_allowed_origins=" https://app.example , https://admin.example ",
        entra_allowed_user_ids=" user-1, user-2 ",  # ty: ignore[unknown-argument] — populate_by_name=True lets callers use the Python field name; ty doesn't model this
        entra_allowed_group_ids=["group-1", " group-2 ", ""],  # ty: ignore[unknown-argument]
    )
    assert cfg.cors_origins_list == ["https://app.example", "https://admin.example"]
    assert cfg.entra_allowed_user_ids_list == ["user-1", "user-2"]
    assert cfg.entra_allowed_group_ids_list == ["group-1", "group-2"]


def test_server_runtime_config_applies_environment_aware_defaults(clean_runtime_env, monkeypatch):
    config_module = importlib.import_module("fleet_rlm.api.config")

    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("FLEET_RLM_SERVE_UI", raising=False)
    monkeypatch.delenv("FLEET_RLM_EXPOSE_DOCS", raising=False)
    monkeypatch.delenv("FLEET_RLM_EXPOSE_ROOT", raising=False)

    staging = config_module.ServerRuntimeConfig(app_env="staging")
    assert staging.database_required is True
    assert staging.allow_debug_auth is False
    assert staging.allow_query_auth_tokens is False
    assert staging.serve_ui is False
    assert staging.expose_docs is True
    assert staging.expose_root is True

    entra = config_module.ServerRuntimeConfig(app_env="staging", auth_mode="entra")
    assert entra.allow_query_auth_tokens is True
    assert entra.expose_docs is False
    assert entra.expose_root is False


def test_server_runtime_config_rejects_invalid_model_identifier(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    with pytest.raises(ValueError, match="provider prefix"):
        config_module.ServerRuntimeConfig(agent_model="gpt-4o")  # ty: ignore[unknown-argument]


def test_validate_startup_or_raise_requires_database_url_when_database_is_required(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        database_required=True,
        database_url=None,  # ty: ignore[unknown-argument]
    )

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_rejects_insecure_staging_configuration(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        app_env="staging",
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        auth_required=True,
        allow_debug_auth=False,
        allow_query_auth_tokens=False,
        cors_allowed_origins=["*"],
        dev_jwt_secret="custom-secret",
    )

    with pytest.raises(ValueError, match=r"CORS_ALLOWED_ORIGINS cannot contain '\*'"):
        cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_requires_entra_configuration(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        app_env="production",
        auth_mode="entra",
        auth_required=True,
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        allow_debug_auth=False,
        entra_jwks_url="https://login.example/jwks",
        entra_audience="api://fleet-rlm",
        entra_issuer_url="https://login.microsoftonline.com/{tenantid}/v2.0",  # ty: ignore[unknown-argument]
        entra_allowed_user_ids=["user-1"],  # ty: ignore[unknown-argument]
        expose_docs=False,  # ty: ignore[unknown-argument]
        expose_root=False,  # ty: ignore[unknown-argument]
        cors_allowed_origins=["https://app.example"],
    )

    with pytest.raises(ValueError, match=r"ENTRA_ISSUER_URL must be a fixed issuer URL"):
        cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_accepts_valid_entra_configuration(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        app_env="production",
        auth_mode="entra",
        auth_required=True,
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        allow_debug_auth=False,
        allow_query_auth_tokens=True,
        entra_jwks_url="https://login.example/jwks",
        entra_audience="api://fleet-rlm",
        entra_issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        entra_allowed_user_ids=["user-1"],  # ty: ignore[unknown-argument]
        expose_docs=False,  # ty: ignore[unknown-argument]
        expose_root=False,  # ty: ignore[unknown-argument]
        cors_allowed_origins=["https://app.example"],
    )

    cfg.validate_startup_or_raise()


def test_validate_startup_or_raise_requires_database_for_neon(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        auth_mode="neon",
        auth_required=True,
        database_required=False,
        neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth",
    )

    with pytest.raises(ValueError, match="DATABASE_REQUIRED must be true when AUTH_MODE=neon"):
        cfg.validate_startup_or_raise()


def test_neon_auth_mode_defaults_database_required(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        auth_mode="neon",
        auth_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth",
    )

    assert cfg.database_required is True


def test_from_app_config_defaults_database_required_for_neon(clean_runtime_env, monkeypatch):
    config_module = importlib.import_module("fleet_rlm.api.config")
    env_module = importlib.import_module("fleet_rlm.integrations.config.env")

    monkeypatch.setenv("AUTH_MODE", "neon")
    monkeypatch.setenv("DATABASE_URL", "postgresql://env.example.invalid/db")

    cfg = config_module.ServerRuntimeConfig.from_app_config(
        env_module.AppConfig(
            database=env_module.DatabaseConfig(
                required=False,
            ),
        ),
    )

    assert cfg.database_required is True
    assert cfg.database_url == "postgresql://env.example.invalid/db"


def test_validate_startup_or_raise_accepts_valid_neon_configuration(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.ServerRuntimeConfig(
        app_env="production",
        auth_mode="neon",
        auth_required=True,
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        allow_debug_auth=False,
        allow_query_auth_tokens=False,
        neon_auth_url="https://ep-xxx.neonauth.us-east-1.aws.neon.tech/neondb/auth",
        neon_tenant_claim="fleet-prod",
        cors_allowed_origins=["https://app.example"],
    )

    cfg.validate_startup_or_raise()
