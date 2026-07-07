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
    assert defaults.rlm_max_iterations == 15
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
    assert cfg.database_required is True


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


def test_app_config_normalizes_auth_required_string_and_sets_auth_mode_dev(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        auth_required="false",
    )
    assert cfg.auth_required is False
    assert cfg.auth_mode == "dev"


def test_validate_startup_or_raise_rejects_auth_mode_dev_in_hosted_env(clean_runtime_env):
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(
        app_env="production",
        auth_required=True,
        auth_mode="dev",
        database_required=True,
        database_url="postgresql://example.invalid/db",  # ty: ignore[unknown-argument]
        secret_encryption_key="some-key",
        cors_allowed_origins=["https://app.example"],
    )

    with pytest.raises(ValueError, match="AUTH_MODE=dev is not allowed when APP_ENV is staging/production"):
        cfg.validate_startup_or_raise()


# ---------------------------------------------------------------------------
# VAL-CONFIG-001: Field exists on AppConfig
# ---------------------------------------------------------------------------


def test_appconfig_has_execution_backend_field(clean_runtime_env):
    """AppConfig must declare a field named execution_backend typed as ExecutionBackend."""
    import importlib

    config_module = importlib.import_module("fleet_rlm.api.config")

    fields = config_module.AppConfig.model_fields
    assert "execution_backend" in fields, f"execution_backend not in model_fields: {list(fields.keys())}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-002: Default value is ExecutionBackend.legacy_agent_runtime
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_default(clean_runtime_env, monkeypatch):
    """Instantiating AppConfig() with no EXECUTION_BACKEND must yield the default."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    monkeypatch.delenv("EXECUTION_BACKEND", raising=False)

    config_module = importlib.import_module("fleet_rlm.api.config")
    cfg = config_module.AppConfig()

    assert cfg.execution_backend is ExecutionBackend.legacy_agent_runtime, (
        f"Expected legacy_agent_runtime, got {cfg.execution_backend}"
    )


# ---------------------------------------------------------------------------
# VAL-CONFIG-003: Env var EXECUTION_BACKEND overrides the default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("direct_rlm", "direct_rlm"),
        ("legacy_agent_runtime", "legacy_agent_runtime"),
    ],
)
def test_appconfig_execution_backend_env_override(
    clean_runtime_env,
    monkeypatch,
    env_value,
    expected,
):
    """Setting EXECUTION_BACKEND env var must parse into the correct enum member."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    monkeypatch.setenv("EXECUTION_BACKEND", env_value)

    config_module = importlib.import_module("fleet_rlm.api.config")
    cfg = config_module.AppConfig()

    expected_member = getattr(ExecutionBackend, expected)
    assert cfg.execution_backend is expected_member, f"Expected {expected_member}, got {cfg.execution_backend}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-004: Invalid env value raises ValidationError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_value",
    [
        "foo",
        "",
        " ",
        "unknown_backend",
    ],
)
def test_appconfig_execution_backend_invalid_env_raises(
    clean_runtime_env,
    monkeypatch,
    invalid_value,
):
    """An invalid EXECUTION_BACKEND value must raise pydantic.ValidationError."""
    import importlib

    from pydantic import ValidationError

    monkeypatch.setenv("EXECUTION_BACKEND", invalid_value)

    config_module = importlib.import_module("fleet_rlm.api.config")

    with pytest.raises(ValidationError) as excinfo:
        config_module.AppConfig()

    # The error must mention the field (either by alias EXECUTION_BACKEND or by name execution_backend)
    errors = excinfo.value.errors()
    assert any(
        "execution_backend" in str(err.get("loc", ())) or "EXECUTION_BACKEND" in str(err.get("loc", ()))
        for err in errors
    ), f"ValidationError must mention execution_backend. Errors: {errors}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-005: Field alias is EXECUTION_BACKEND
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_alias(clean_runtime_env):
    """The field must have alias='EXECUTION_BACKEND' and accept construction via alias."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    config_module = importlib.import_module("fleet_rlm.api.config")

    # Check alias declaration
    field_info = config_module.AppConfig.model_fields["execution_backend"]
    assert field_info.alias == "EXECUTION_BACKEND", f"Expected alias 'EXECUTION_BACKEND', got {field_info.alias!r}"

    # Construction via alias keyword must work
    cfg = config_module.AppConfig(EXECUTION_BACKEND="direct_rlm")  # type: ignore[call-arg]
    assert cfg.execution_backend is ExecutionBackend.direct_rlm, f"Expected direct_rlm, got {cfg.execution_backend}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-006: Field is readable via attribute access
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_attribute_access(clean_runtime_env, monkeypatch):
    """After construction, config.execution_backend must return an ExecutionBackend member."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    monkeypatch.delenv("EXECUTION_BACKEND", raising=False)

    config_module = importlib.import_module("fleet_rlm.api.config")
    cfg = config_module.AppConfig()

    assert isinstance(cfg.execution_backend, ExecutionBackend), (
        f"Expected ExecutionBackend instance, got {type(cfg.execution_backend)}"
    )
    # Verify plain dot access works
    _ = cfg.execution_backend


# ---------------------------------------------------------------------------
# VAL-CONFIG-007: Field does not alter existing AppConfig behavior
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_no_regression_on_existing_fields(
    clean_runtime_env,
    monkeypatch,
):
    """Adding execution_backend must not change defaults of pre-existing fields."""
    import importlib

    monkeypatch.delenv("EXECUTION_BACKEND", raising=False)

    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig()
    assert cfg.app_env == "local"
    assert cfg.rlm_child_isolation_mode == "auto"
    assert cfg.sandbox_provider == "daytona"


# ---------------------------------------------------------------------------
# VAL-CONFIG-008: model_validator does not strip or rewrite execution_backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["local", "staging", "production"])
def test_appconfig_execution_backend_not_env_aware(
    clean_runtime_env,
    monkeypatch,
    env,
):
    """execution_backend must always resolve to the default regardless of app_env."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.delenv("EXECUTION_BACKEND", raising=False)

    config_module = importlib.import_module("fleet_rlm.api.config")
    cfg = config_module.AppConfig()

    assert cfg.execution_backend is ExecutionBackend.legacy_agent_runtime, (
        f"With APP_ENV={env}, expected legacy_agent_runtime, got {cfg.execution_backend}"
    )

    # Also verify env override still works regardless of app_env
    monkeypatch.setenv("EXECUTION_BACKEND", "direct_rlm")
    cfg2 = config_module.AppConfig()
    assert cfg2.execution_backend is ExecutionBackend.direct_rlm, (
        f"With APP_ENV={env} and EXECUTION_BACKEND=direct_rlm, expected direct_rlm, got {cfg2.execution_backend}"
    )


# ---------------------------------------------------------------------------
# VAL-CONFIG-009: Field type annotation is ExecutionBackend, not a string literal
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_annotation_is_enum(clean_runtime_env):
    """The annotation stored on the field must be ExecutionBackend, not str or Literal."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    config_module = importlib.import_module("fleet_rlm.api.config")

    field_info = config_module.AppConfig.model_fields["execution_backend"]
    annotation = field_info.annotation

    assert annotation is ExecutionBackend, f"Expected annotation to be ExecutionBackend, got {annotation}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-010: Field round-trips through model_dump and model_validate
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_roundtrip(clean_runtime_env):
    """model_validate with string value must produce correct member, and
    model_dump must serialize it back to the string."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    config_module = importlib.import_module("fleet_rlm.api.config")

    # Round-trip via model_validate and model_dump
    instance = config_module.AppConfig.model_validate({"execution_backend": "direct_rlm"})
    assert instance.execution_backend is ExecutionBackend.direct_rlm, (
        f"Expected direct_rlm, got {instance.execution_backend}"
    )

    dump = instance.model_dump()
    assert dump["execution_backend"] == "direct_rlm", f"Expected 'direct_rlm' string, got {dump['execution_backend']!r}"

    # Also test round-trip for the default value
    default_instance = config_module.AppConfig.model_validate({})
    assert default_instance.execution_backend is ExecutionBackend.legacy_agent_runtime
    default_dump = default_instance.model_dump()
    assert default_dump["execution_backend"] == "legacy_agent_runtime"


# ---------------------------------------------------------------------------
# VAL-CONFIG-011: EXECUTION_BACKEND parsing is exact and case-sensitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_value",
    [
        "DIRECT_RLM",
        "Direct_Rlm",
        "Legacy_Agent_Runtime",
        " legacy_agent_runtime",
        "legacy_agent_runtime ",
        " direct_rlm ",
        "LEGACY_AGENT_RUNTIME",
    ],
)
def test_appconfig_execution_backend_is_exact_case_sensitive(
    clean_runtime_env,
    monkeypatch,
    invalid_value,
):
    """Case variants and whitespace-padded values must raise ValidationError."""
    import importlib

    from pydantic import ValidationError

    monkeypatch.setenv("EXECUTION_BACKEND", invalid_value)

    config_module = importlib.import_module("fleet_rlm.api.config")

    with pytest.raises(ValidationError) as excinfo:
        config_module.AppConfig()

    # The error must mention the field (either by alias EXECUTION_BACKEND or by name execution_backend)
    errors = excinfo.value.errors()
    assert any(
        "execution_backend" in str(err.get("loc", ())) or "EXECUTION_BACKEND" in str(err.get("loc", ()))
        for err in errors
    ), f"ValidationError must mention execution_backend/EXECUTION_BACKEND. Errors: {errors}"


# ---------------------------------------------------------------------------
# VAL-CONFIG-012: JSON-mode model_dump serializes execution_backend as a string
# ---------------------------------------------------------------------------


def test_appconfig_execution_backend_json_dump_is_string(
    clean_runtime_env,
    monkeypatch,
):
    """model_dump(mode='json') must serialize the enum as its canonical string value."""
    import importlib

    from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

    config_module = importlib.import_module("fleet_rlm.api.config")

    # Default config
    monkeypatch.delenv("EXECUTION_BACKEND", raising=False)
    default_cfg = config_module.AppConfig()
    assert default_cfg.execution_backend is ExecutionBackend.legacy_agent_runtime
    json_dump = default_cfg.model_dump(mode="json")
    assert json_dump["execution_backend"] == "legacy_agent_runtime", (
        f"Expected 'legacy_agent_runtime', got {json_dump['execution_backend']!r}"
    )

    # Direct RLM config via alias
    direct_cfg = config_module.AppConfig(EXECUTION_BACKEND="direct_rlm")  # type: ignore[call-arg]
    assert direct_cfg.execution_backend is ExecutionBackend.direct_rlm
    json_dump2 = direct_cfg.model_dump(mode="json")
    assert json_dump2["execution_backend"] == "direct_rlm", (
        f"Expected 'direct_rlm', got {json_dump2['execution_backend']!r}"
    )
