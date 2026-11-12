"""Configuration precedence, model gateways, and native runtime integration contracts."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import dspy
import httpx
import pytest
from dspy.primitives.facade import SandboxLM
from pydantic import BaseModel, ValidationError

from fleet_rlm.config import FleetSettings, resolve_turn_deadline
from fleet_rlm.contracts import TRIAGE_ROUTE, decision_evidence, item_decision, make_choice_type
from fleet_rlm.engine.compiled import program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.signatures import FleetTask
from fleet_rlm.models import build_lm, complete_text
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    add_optimization_arguments,
    read_worker_settings,
    worker_settings_payload,
)
from fleet_rlm.optimize import artifacts
from fleet_rlm.optimize.scorers import SCORER_VERSION
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, LOCK_PATH

# Configuration precedence and provenance


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    # These tests exercise .env precedence from a private tmp_path working directory.
    monkeypatch.setitem(FleetSettings.model_config, "env_file", ".env")


def toml(text):
    Path("config").mkdir(exist_ok=True)
    Path("config/fleet.toml").write_text("[config]\nschema_version = 5\n" + text)


@pytest.mark.usefixtures("isolated_settings")
def test_sources_and_aliases_have_one_precedence(monkeypatch):
    toml("[runtime]\nmax_llm_calls_per_turn = 41\n")
    assert FleetSettings(_env_file=None).max_llm_calls_per_turn == 41
    Path(".env").write_text("FLEET_MAX_LLM_CALLS_PER_TURN=42\n")
    settings = FleetSettings()
    assert settings.max_llm_calls_per_turn == 42
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "dotenv"
    monkeypatch.setenv("MAX_LLM_CALLS_PER_TURN", "43")
    settings = FleetSettings()
    assert settings.max_llm_calls_per_turn == 43
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "environment"
    settings = FleetSettings(max_llm_calls_per_turn=44)
    assert settings.max_llm_calls_per_turn == 44
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "explicit"


@pytest.mark.usefixtures("isolated_settings")
def test_optional_default_file_and_explicit_path(monkeypatch, tmp_path):
    assert FleetSettings(_env_file=None).configuration()["sources"]["execution_mode"] == "default"
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(ValueError, match="existing TOML"):
        FleetSettings(_env_file=None)
    p = tmp_path / "selected.toml"
    p.write_text('[config]\nschema_version=5\n[runtime]\nexecution_mode="rlm"')
    monkeypatch.setenv("FLEET_CONFIG", str(p))
    assert FleetSettings(_env_file=None).execution_mode == "rlm"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    "content",
    [
        "[runtime]\nturn_timeout_seconds = 9",
        '[runtime]\nturn_timeout_seconds = "900"',
        "[runtime]\nturn_timeout_seconds = true",
        "[runtime]\nwrap_up_seconds = 300",
        "[llm.root]\nmax_tokens = 0",
        "[llm.sub]\ntemperature = 3",
        "[llm.reflection]\nnum_retries = -1",
        '[llm.root]\ncache = "false"',
        '[daytona]\napi_key = "credential-marker"',
        '[storage]\ndatabase_url = "credential-marker"',
        '[gateway]\ntoken = "credential-marker"',
        '[llm.root]\napi_key_env = "credential-marker"',
        '[unknown]\nx = "credential-marker"',
    ],
)
def test_invalid_toml_fails_without_echoing_values(content):
    toml(content)
    with pytest.raises(ValueError) as error:
        FleetSettings(_env_file=None)
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", [0, 10, 3600])
def test_turn_timeout_accepts_zero_or_bounded_integers(value):
    assert FleetSettings(_env_file=None, turn_timeout_seconds=value).turn_timeout_seconds == value


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", [9, -1, True, 0.5, 3601, "600"])
def test_turn_timeout_rejects_other_shapes(value):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, turn_timeout_seconds=value)
    message = str(error.value)
    assert "turn_timeout_seconds" in message
    assert "0 or between 10 and 3600" in message


@pytest.mark.usefixtures("isolated_settings")
def test_turn_timeout_environment_text_and_zero_agree_with_toml(monkeypatch):
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "0")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "600")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600
    toml("[runtime]\nturn_timeout_seconds = 0")
    monkeypatch.delenv("FLEET_TURN_TIMEOUT_SECONDS")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", ["true", "0.5", '"600"', "-1", "9", "3601"])
def test_toml_turn_timeout_stays_a_bounded_integer(value):
    toml(f"[runtime]\nturn_timeout_seconds = {value}")
    with pytest.raises(ValueError):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    ("configured", "configured_source", "requested", "expected"),
    [
        (900, "default", None, ("default", 0)),
        (1200, "toml", None, ("configuration", 1200)),
        (1200, "environment", 600, ("request", 600)),
        (1200, "toml", 0, ("request", None)),
        (1200, "toml", None, ("configuration", 1200)),
        (900, "default", 3600, ("request", 3600)),
    ],
)
def test_deadline_resolution_follows_request_then_configuration_then_default(
    configured, configured_source, requested, expected
):
    policy = resolve_turn_deadline(requested, configured, configured_source=configured_source)
    assert (policy.source, policy.effective_seconds) == expected


@pytest.mark.usefixtures("isolated_settings")
def test_schema_and_parse_errors(monkeypatch):
    monkeypatch.setenv("FLEET_CONFIG", "file.toml")
    for text in ("x=[", "[config]\nschema_version=2", "[config]\nschema_version=5\nextra=1"):
        Path("file.toml").write_text(text)
        with pytest.raises(ValueError):
            FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
def test_role_settings_are_applied_by_native_model_builder(monkeypatch):
    from fleet_rlm.models import build_lm

    toml("[llm.root]\nmax_tokens=16384\ntemperature=0.0\nnum_retries=1\ncache=true\n[llm.sub]\nmax_tokens=8192\n")
    calls = []
    monkeypatch.setattr(
        "fleet_rlm.models.dspy.LM",
        lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model, kwargs=kwargs),
    )
    settings = FleetSettings(
        _env_file=None,
    )
    settings.fleet_root_model, settings.fleet_sub_model, settings.fleet_reflection_model = "root", "sub", "reflection"
    build_lm(settings, "root", "root")
    build_lm(settings, "sub", "sub")
    build_lm(settings, "reflection", "reflection")
    assert calls[0][1]["max_tokens"] == 16384
    assert calls[0][1]["temperature"] == 0
    assert calls[0][1]["cache"] is True and calls[0][1]["num_retries"] == 1
    assert calls[1][1]["max_tokens"] == 8192
    assert "temperature" not in calls[1][1]
    assert calls[2][1]["max_tokens"] == 32000
    assert all(kwargs["engine"] == "lm15" for _, kwargs in calls)


@pytest.mark.usefixtures("isolated_settings")
def test_build_jev_lm_returns_none_without_typesafe_key():
    assert FleetSettings(_env_file=None, typesafe_api_key="").build_jev_lm() is None


@pytest.mark.usefixtures("isolated_settings")
def test_build_jev_lm_builds_the_configured_typesafe_model():
    import dspy

    lm = FleetSettings(_env_file=None, typesafe_api_key="credential-marker", typesafe_model="jev-1.14.0").build_jev_lm()
    assert isinstance(lm, dspy.LM)
    assert lm.model == "typesafe:jev-1.14.0"
    assert lm.supports_decision_requests is True
    assert lm.cache is False and lm.engine == "lm15"
    assert lm.kwargs["api_key"] == "credential-marker"


@pytest.mark.usefixtures("isolated_settings")
def test_shipped_defaults_cap_root_output_but_not_sub(monkeypatch):
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    settings = FleetSettings(_env_file=None)
    assert settings.fleet_root_max_tokens == 16384
    assert settings.fleet_sub_max_tokens is None
    for role in ("root", "sub"):
        assert settings.resolved_model(role).model == "openai/system.ai.deepseek-v4-1-flash-1017"


@pytest.mark.usefixtures("isolated_settings")
def test_root_and_sub_default_to_deepseek_without_changing_other_roles():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://gateway.example/v1",
        fleet_llm_api_key="default-secret",
    )
    for role in ("root", "sub"):
        resolved = settings.resolved_model(role)
        assert resolved.model == "openai/system.ai.deepseek-v4-1-flash-1017"
        assert resolved.api_base == "https://gateway.example/v1"
        assert resolved.api_key.get_secret_value() == "default-secret"
    for role in ("shared", "reflection", "judge"):
        assert settings.resolved_model(role).model == "openai/gpt-4o-mini"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("source", ["toml", "dotenv", "environment", "explicit"])
def test_explicit_shared_model_still_overrides_root_and_sub_defaults(source, monkeypatch):
    if source == "toml":
        toml('[llm]\nmodel = "openai/gpt-4o-mini"\n')
    elif source == "dotenv":
        Path(".env").write_text("FLEET_LLM_MODEL=openai/gpt-4o-mini\n")
    elif source == "environment":
        monkeypatch.setenv("FLEET_LLM_MODEL", "openai/gpt-4o-mini")
    settings = FleetSettings(**({"fleet_llm_model": "openai/gpt-4o-mini"} if source == "explicit" else {}))
    for role in ("root", "sub"):
        assert settings.resolved_model(role).model == "openai/gpt-4o-mini"
    overridden = FleetSettings(fleet_sub_model="anthropic/claude-sonnet-4-5")
    assert overridden.resolved_model("sub").model == "anthropic/claude-sonnet-4-5"


@pytest.mark.usefixtures("isolated_settings")
def test_public_configuration_omits_credentials_and_tracks_session_override():
    settings = FleetSettings(
        _env_file=None,
        databricks_token="credential-marker",
        fleet_database_url="postgresql://credential-marker",
        fleet_daytona_api_key="credential-marker",
        typesafe_api_key="credential-marker",
    )
    public = settings.configuration()
    assert "credential-marker" not in str(public)
    changed = settings.configuration({"execution_mode": "rlm"})
    assert changed["sources"]["execution_mode"] == "session"
    assert public["digest"] != changed["digest"]
    assert settings.configuration()["digest"] == public["digest"]
    assert changed["file_changes_require_restart"] is True
    assert settings.configuration({"execution_mode": settings.execution_mode})["sources"]["execution_mode"] == "session"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("role", ["root", "sub", "reflection", "judge"])
@pytest.mark.parametrize(
    ("option", "shared"), [("max_tokens", 100), ("temperature", 0.4), ("num_retries", 1), ("cache", False)]
)
def test_digest_distinguishes_inherited_generation_from_explicit_role_defaults(role, option, shared):
    # Use the opposite cache value where the role default is already false.
    field = f"fleet_{role}_{option}"
    default = FleetSettings.model_fields[field].get_default()
    if option == "cache":
        shared = not default
    inherited = FleetSettings(_env_file=None, **{f"fleet_llm_{option}": shared})
    explicit = FleetSettings(_env_file=None, **{f"fleet_llm_{option}": shared, field: default})

    assert inherited.model_dump() == explicit.model_dump()
    assert getattr(inherited.resolved_model(role), option) == shared
    assert getattr(explicit.resolved_model(role), option) == default
    assert inherited.configuration()["digest"] != explicit.configuration()["digest"]


@pytest.mark.usefixtures("isolated_settings")
def test_digest_ignores_source_only_changes_and_model_credentials():
    inherited = FleetSettings(_env_file=None, fleet_llm_api_key="first-secret")
    explicit = FleetSettings(_env_file=None, fleet_llm_api_key="second-secret", fleet_root_max_tokens=16384)
    first, second = inherited.configuration(), explicit.configuration()

    assert first["sources"]["fleet_root_max_tokens"] != second["sources"]["fleet_root_max_tokens"]
    assert first["values"] == second["values"]
    assert first["digest"] == second["digest"]
    assert "first-secret" not in str(first) and "second-secret" not in str(second)
    assert set(first) == {
        "schema_version",
        "digest",
        "values",
        "sources",
        "mlflow_target",
        "file_changes_require_restart",
    }
    assert first["schema_version"] == 5


@pytest.mark.usefixtures("isolated_settings")
def test_session_digest_uses_route_overrides_without_resolving_connections_again(monkeypatch):
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://models.example/v1",
        fleet_llm_api_key="session-secret",
        fleet_llm_max_tokens=100,
    )
    saved = {"mode": "rlm", "models": {"root": "openai/new-root", "sub": "openai/new-sub"}}
    session = settings.for_session(saved)
    overrides = {"execution_mode": "rlm", "fleet_root_model": "openai/new-root", "fleet_sub_model": "openai/new-sub"}
    monkeypatch.setattr(FleetSettings, "_resolve_models", lambda _self: pytest.fail("must retain snapshots"))

    assert settings.configuration(overrides)["digest"] == session.configuration()["digest"]
    assert settings.configuration(overrides)["digest"] != settings.configuration()["digest"]
    for role in ("root", "sub"):
        assert session.resolved_model(role).api_base == "https://models.example/v1"
        assert session.resolved_model(role).api_key.get_secret_value() == "session-secret"
        assert session.resolved_model(role).max_tokens == 100
        assert settings.resolved_model(role).model == "openai/system.ai.deepseek-v4-1-flash-1017"


@pytest.mark.usefixtures("isolated_settings")
def test_lakebase_oauth_requires_endpoint_and_password_free_tls_dsn():
    endpoint = "projects/fleet-rlm/branches/production/endpoints/primary"
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://fleet_app@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_lakebase_endpoint=endpoint,
    )
    assert settings.fleet_lakebase_endpoint == endpoint
    with pytest.raises(ValidationError, match="sslmode=require"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://fleet_app@ep.example.databricks.com/fleet_rlm_lite",
            fleet_lakebase_endpoint=endpoint,
        )
    with pytest.raises(ValidationError, match="embedded password"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://fleet_app:password@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
            fleet_lakebase_endpoint=endpoint,
        )


@pytest.mark.usefixtures("isolated_settings")
def test_database_schema_is_a_safe_identifier_and_migration_url_is_not_public_configuration():
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://runtime@db.example/fleet",
        fleet_database_schema="fleet_rlm_lite",
        fleet_database_migration_url="postgresql://migration:secret@db.example/fleet",
    )
    public = settings.configuration()
    assert public["values"]["fleet_database_schema"] == "fleet_rlm_lite"
    assert "fleet_database_migration_url" not in public["values"]
    assert "secret" not in str(public)
    with pytest.raises(ValidationError, match="lowercase PostgreSQL identifier"):
        FleetSettings(_env_file=None, fleet_database_schema="bad-name")
    with pytest.raises(ValidationError, match="must target the runtime database"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://runtime@db.example/fleet",
            fleet_database_migration_url="postgresql://migrator@other.example/fleet",
        )


@pytest.mark.usefixtures("isolated_settings")
def test_lakebase_migration_dsn_uses_the_same_tls_database_with_a_separate_role():
    endpoint = "projects/fleet-rlm/branches/production/endpoints/primary"
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://runtime@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_database_migration_url="postgresql://migrator@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_lakebase_endpoint=endpoint,
    )
    assert settings.fleet_database_schema == "public"
    with pytest.raises(ValidationError, match="must target the runtime database"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://runtime@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
            fleet_database_migration_url="postgresql://migrator@ep.example.databricks.com/other?sslmode=require",
            fleet_lakebase_endpoint=endpoint,
        )


@pytest.mark.usefixtures("isolated_settings")
def test_validation_errors_hide_input_values():
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, fleet_root_temperature="credential-marker")
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("field", ["databricks_host", "fleet_llm_api_base", "mlflow_databricks_tracking_uri"])
def test_public_urls_cannot_expose_credentials(field):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, **{field: "https://user:credential-marker@example.invalid"})
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
def test_explicit_cli_copy_is_validated_and_does_not_mutate_original():
    toml("[server]\nport=8001\n")
    settings = FleetSettings(_env_file=None)
    updated = settings.with_explicit_overrides(port=8002)
    assert updated.port == 8002 and settings.port == 8001
    assert updated.configuration()["sources"]["port"] == "explicit"
    assert settings.configuration()["sources"]["port"] == "toml"
    with pytest.raises(ValidationError):
        settings.with_explicit_overrides(port=0)


@pytest.mark.usefixtures("isolated_settings")
def test_configuration_is_frozen_until_new_settings_are_loaded():
    toml("[runtime]\nturn_timeout_seconds=900\n")
    settings = FleetSettings(_env_file=None)
    toml("[runtime]\nturn_timeout_seconds=600\n")
    assert settings.turn_timeout_seconds == 900
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600


@pytest.mark.usefixtures("isolated_settings")
def test_schema_version_must_be_integer():
    Path("config").mkdir()
    Path("config/fleet.toml").write_text("[config]\nschema_version=5.0\n")
    with pytest.raises(ValueError, match="schema_version"):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    "values",
    [
        {"fleet_llm_api_base": "http://example.invalid"},
    ],
)
def test_invalid_routes_fail_during_settings_loading(values):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, **values)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("FLEET_LLM_BASE_URL", "http://example.invalid", "fleet_llm_api_base"),
    ],
)
def test_cli_reports_invalid_configuration_without_traceback(monkeypatch, capsys, name, value, field):
    from fleet_rlm import cli
    from fleet_rlm.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve"])
    get_settings.cache_clear()
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "fleet: error: invalid configuration: " in err
    assert field in err
    assert "Traceback" not in err


@pytest.mark.usefixtures("isolated_settings")
def test_local_mlflow_mode_ignores_databricks_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "databricks://profile")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME", "/Users/someone/fleet")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert target == ("local", "http://127.0.0.1:5001", "fleet-rlm-dev", "fleet-rlm-eval", "fleet-rlm-gepa")


@pytest.mark.usefixtures("isolated_settings")
def test_local_mlflow_mode_keeps_local_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "mine")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert (target.tracking_uri, target.experiment_name) == ("http://127.0.0.1:5002", "mine")


@pytest.mark.usefixtures("isolated_settings")
def test_databricks_mlflow_mode_is_validated_only_when_selected(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", "")
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    with pytest.raises(ValidationError, match="Unity Catalog"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "http://127.0.0.1:5001")
    with pytest.raises(ValidationError, match="databricks tracking URI"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")


@pytest.mark.usefixtures("isolated_settings")
def test_configuration_defaults_to_local_and_keeps_databricks_available():
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    target = FleetSettings(_env_file=None, mlflow_mode="databricks").mlflow_target()
    assert target.mode == "databricks"
    assert target.experiment_name.startswith("/")
    assert target.optimization_experiment_name.startswith("/")


@pytest.mark.usefixtures("isolated_settings")
def test_native_judge_configuration_does_not_export_gateway_environment():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://workspace.example/ai-gateway/mlflow/v1",
        databricks_token="token",
    )
    assert settings.judge_environment() == {}
    assert FleetSettings(_env_file=None, fleet_judge_model="databricks:/endpoint").judge_environment() == {}
    with pytest.raises(ValidationError, match="provider"):
        FleetSettings(_env_file=None, fleet_judge_model="deepseek")


@pytest.mark.usefixtures("isolated_settings")
def test_list_models_without_endpoint_does_not_perform_discovery(monkeypatch):
    monkeypatch.setattr("httpx.stream", lambda *_args, **_kwargs: pytest.fail("unexpected network"))
    assert FleetSettings(_env_file=None).list_models() == []


@pytest.mark.usefixtures("isolated_settings")
def test_checked_in_toml_loads_without_snapshot_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    settings = FleetSettings(_env_file=None)
    assert not hasattr(settings, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in settings.configuration()["values"]
    assert "snapshot" not in settings.configuration()["values"]


@pytest.mark.usefixtures("isolated_settings")
def test_leftover_toml_snapshot_key_raises_unsupported_setting() -> None:
    toml('[daytona]\nsnapshot = "fleet-rlm-python313-v10"\n')
    with pytest.raises(ValueError, match=r"Unsupported or environment-only Fleet TOML setting: daytona\.snapshot"):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
def test_snapshot_environment_variable_creates_no_field_or_digest_change(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    baseline = FleetSettings(_env_file=None)
    monkeypatch.setenv("FLEET_DAYTONA_SNAPSHOT", "fleet-rlm-base")
    overridden = FleetSettings(_env_file=None)
    assert not hasattr(overridden, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in overridden.configuration()["values"]
    assert overridden.configuration()["digest"] == baseline.configuration()["digest"]


@pytest.mark.usefixtures("isolated_settings")
def test_native_chat_policy_does_not_force_provider_capabilities():
    import dspy

    from fleet_rlm.models import build_lm

    settings = FleetSettings(_env_file=None, fleet_llm_model="openai/demo.model")
    assert isinstance(build_lm(settings, "", "root"), dspy.LM)
    assert isinstance(settings.resolved_model("root").build_adapter(), dspy.ChatAdapter)


@pytest.mark.usefixtures("isolated_settings")
def test_databricks_requires_explicit_deployment_identifiers(monkeypatch):
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", raising=False)
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", raising=False)
    assert FleetSettings(_env_file=None).mlflow_mode == "local"
    with pytest.raises(ValueError, match="SQL warehouse ID"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")
    configured = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_trace_catalog="example_catalog",
        mlflow_databricks_tracing_sql_warehouse_id="example-warehouse",
    )
    assert configured.mlflow_tracking_uri == "databricks"


@pytest.mark.usefixtures("isolated_settings")
def test_shared_discovery_uses_default_connection_when_root_has_an_override(monkeypatch):
    from contextlib import nullcontext

    import httpx

    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/shared",
        fleet_llm_api_base="https://shared.example/v1",
        fleet_llm_api_key="shared-test-key",
        fleet_root_model="openai/root",
        fleet_root_api_base="https://root.example/v1",
    )

    def stream(method, url, *, headers, timeout):
        assert method == "GET" and url == "https://shared.example/v1/models"
        assert headers == {"Authorization": "Bearer shared-test-key"}
        assert timeout == 10
        return nullcontext(httpx.Response(200, json={"data": [{"id": "shared"}]}, request=httpx.Request(method, url)))

    monkeypatch.setattr(httpx, "stream", stream)
    assert settings.list_models(role="shared") == ["openai/shared"]


@pytest.mark.usefixtures("isolated_settings")
def test_an_explicit_judge_pairs_the_mlflow_openai_scorers_with_its_own_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "judge-key")
    judge = {
        "fleet_correctness_model": "openai/deepseek/deepseek-v4.1-flash",
        "fleet_judge_api_base": "https://api.commandcode.ai/provider/v1",
        "fleet_judge_api_key_env": "COMMAND_CODE_API_KEY",
    }
    settings = FleetSettings(_env_file=None, fleet_judge_model="openai:/deepseek/deepseek-v4.1-flash", **judge)
    assert settings.judge_environment() == {
        "OPENAI_API_BASE": "https://api.commandcode.ai/provider/v1",
        "OPENAI_API_KEY": "judge-key",
    }
    # The DSPy judge keeps its own connection, separate from serving.
    assert settings.resolved_model("judge").api_base == "https://api.commandcode.ai/provider/v1"
    assert settings.resolved_model("judge").api_key.get_secret_value() == "judge-key"
    # Other providers use their native environment even when the judge role is explicit.
    other = FleetSettings(_env_file=None, fleet_judge_model="databricks:/endpoint", **judge)
    assert other.judge_environment() == {}


@pytest.mark.usefixtures("isolated_settings")
def test_inherited_judge_connection_exports_resolved_credential(monkeypatch):
    monkeypatch.setenv("INHERITED_JUDGE_KEY", "test-only-key")
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://provider.example/v1",
        fleet_llm_api_key_env="INHERITED_JUDGE_KEY",
        fleet_judge_model="openai:/configured-model",
    )
    assert settings.judge_environment() == {
        "OPENAI_API_BASE": "https://provider.example/v1",
        "OPENAI_API_KEY": "test-only-key",
    }


# Gateway model resolution


def test_databricks_mlflow_configuration_requires_governed_trace_storage() -> None:
    with pytest.raises(ValueError, match="Unity Catalog"):
        FleetSettings(
            mlflow_mode="databricks",
            mlflow_databricks_tracking_uri="databricks",
            mlflow_databricks_trace_catalog="",
            mlflow_databricks_trace_schema="",
            mlflow_databricks_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        mlflow_databricks_experiment_name="/Shared/fleet-rlm",
        mlflow_databricks_trace_catalog="fleet",
        mlflow_databricks_trace_schema="traces",
        mlflow_databricks_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_profile_specific_mlflow_environment_ignores_sdk_destination(monkeypatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)
    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5002"
    assert settings.mlflow_experiment_name == "fleet-test"


def test_default_model_and_independent_role_connection(monkeypatch):
    monkeypatch.setenv("SUB_KEY", "sub-secret")
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/default",
        fleet_llm_api_base="http://localhost:8000/v1",
        fleet_llm_api_key="default-secret",
        fleet_sub_model="anthropic/custom",
        fleet_sub_api_key_env="SUB_KEY",
    )
    assert settings.resolved_model("root").model == "openai/default"
    assert settings.resolved_model("reflection").api_base == "http://localhost:8000/v1"
    assert settings.resolved_model("sub").api_base == ""
    assert settings.resolved_model("sub").api_key.get_secret_value() == "sub-secret"
    assert settings.resolved_model("judge").temperature == 0
    calls = []
    monkeypatch.setattr(
        dspy, "LM", lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model)
    )
    build_lm(settings, "", "root")
    assert calls[0][1]["engine"] == "lm15"
    assert calls[0][1]["api_key"] == "default-secret"
    assert not hasattr(build_lm(settings, "", "root"), "fleet_model_id")


def test_secret_snapshot_survives_session_rebuild_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("CUSTOM_KEY=first-value\n")
    settings = FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
    path.write_text("CUSTOM_KEY=second-value\n")
    monkeypatch.setenv("CUSTOM_KEY", "environment-value")
    updated = settings.for_session({"models": {"root": "openai/new", "sub": "openai/child"}})
    assert updated.resolved_model("root").api_key.get_secret_value() == "first-value"
    assert updated.resolved_model("sub").model == "openai/child"
    assert (
        FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
        .resolved_model("root")
        .api_key.get_secret_value()
        == "environment-value"
    )
    assert "first-value" not in str(settings.configuration())
    assert "first-value" not in repr(settings)


@pytest.mark.parametrize(
    "endpoint", ["http://remote.example/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=x"]
)
def test_invalid_endpoints_fail_during_settings_load(endpoint):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, fleet_llm_api_base=endpoint)


@pytest.mark.parametrize("table", ["models", "connections"])
def test_obsolete_empty_registry_tables_fail(tmp_path, monkeypatch, table):
    path = tmp_path / "settings.toml"
    path.write_text(f"[config]\nschema_version=5\n[{table}]\n")
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    with pytest.raises(ValueError, match="removed"):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize(
    "payload", [{"data": [{"id": "foo/bar"}]}, {"data": []}, {"wrong": []}, {"data": [{"id": 123}]}]
)
def test_optional_discovery_validates_response_and_role(monkeypatch, payload):
    from contextlib import contextmanager

    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        yield httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(
        _env_file=None, fleet_sub_model="openai/sub", fleet_sub_api_base="https://models.example/v1"
    )
    assert settings.list_models(role="root") == []
    if payload.get("data") in ([], [{"id": "foo/bar"}]):
        assert settings.list_models(role="sub") == (["openai/foo/bar"] if payload["data"] else [])
    else:
        with pytest.raises(ValueError):
            settings.list_models(role="sub")
    assert calls[-1][1] == "https://models.example/v1/models"


def test_complete_text_preserves_generation_defaults():
    calls = []
    lm = SimpleNamespace(model="openai/test", kwargs={"temperature": 0.2, "max_tokens": 300})

    class Fake:
        model = lm.model
        kwargs = lm.kwargs

        def __call__(self, request):
            calls.append(request)
            return SimpleNamespace(text="OK")

    assert complete_text(Fake(), "prompt", max_tokens=16) == "OK"
    assert calls[0].config.max_tokens == 16
    assert calls[0].config.temperature == 0.2


def test_native_auth_is_deferred_to_dspy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "native-secret")
    settings = FleetSettings(_env_file=None, fleet_llm_model="openai/test")
    calls = []
    monkeypatch.setattr(dspy, "LM", lambda model, **kwargs: calls.append(kwargs) or SimpleNamespace(model=model))
    build_lm(settings, "", "root")
    assert "api_key" not in calls[0]


def test_global_generation_defaults_and_explicit_role_override(tmp_path, monkeypatch):
    path = tmp_path / "fleet.toml"
    path.write_text(
        '[config]\nschema_version=5\n[llm]\nmodel="openai/test"\nmax_tokens=100\n'
        "temperature=0.4\ncache=true\n[llm.sub]\nmax_tokens=200\n"
    )
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    settings = FleetSettings(_env_file=None)
    assert settings.resolved_model("root").max_tokens == 100
    assert settings.resolved_model("sub").max_tokens == 200
    assert settings.resolved_model("reflection").temperature == 0.4
    assert settings.resolved_model("judge").cache


@pytest.mark.parametrize("failure", ["auth", "timeout", "oversized"])
def test_discovery_transport_and_size_failures(monkeypatch, failure):
    from contextlib import contextmanager

    @contextmanager
    def stream(method, url, **_kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private detail")
        response = httpx.Response(
            401 if failure == "auth" else 200,
            content=b"x" * 1_000_001 if failure == "oversized" else b"private detail",
            request=httpx.Request(method, url),
        )
        yield response

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(_env_file=None, fleet_llm_api_base="http://127.0.0.1:8000/v1")
    with pytest.raises((httpx.HTTPError, ValueError)):
        settings.list_models()


@pytest.mark.parametrize(
    ("model", "wire_format"),
    [("openai/deepseek/deepseek-v4.1-flash", "json_object"), ("openai/gpt-4o-mini", "json_schema")],
)
def test_explicit_json_mode_reaches_native_request(model, wire_format):
    from dspy._vendor.lm15.providers.openai_chat import _response_format_to_chat
    from dspy.clients.execution import prepare

    settings = FleetSettings(
        _env_file=None,
        fleet_root_model=model,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    lm = build_lm(settings, "", "root")
    assert lm.kwargs["response_format"] == {"type": "json_object"}
    kwargs = {}
    settings.resolved_model("root").build_adapter()._prepare_response_format(
        lm, kwargs, dspy.Signature("task -> reasoning, code")
    )
    call = prepare(lm, "Return JSON", kwargs)
    request = call.request
    wire = _response_format_to_chat(request.config.response_format)
    assert wire["type"] == wire_format
    if wire_format == "json_object":
        assert wire == {"type": "json_object"}
    else:
        assert wire["json_schema"]["strict"] is True
        assert set(wire["json_schema"]["schema"]["required"]) == {"reasoning", "code"}


def test_response_format_inheritance_validation_and_digest():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_adapter="json",
        fleet_llm_response_format="json_object",
        fleet_sub_response_format="auto",
    )
    assert settings.resolved_model("root").response_format == "json_object"
    assert settings.resolved_model("sub").response_format == "auto"
    baseline = FleetSettings(_env_file=None)
    assert settings.configuration()["digest"] != baseline.configuration()["digest"]
    with pytest.raises(ValueError, match="JSON adapter"):
        FleetSettings(_env_file=None, fleet_root_response_format="json_object")


def test_response_format_sources_and_session_snapshot(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[llm]\nadapter="json"\nresponse_format="json_object"\n')
    env = tmp_path / ".env"
    env.write_text("FLEET_SUB_RESPONSE_FORMAT=auto\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_ROOT_RESPONSE_FORMAT", "auto")
    settings = FleetSettings(_env_file=env, fleet_judge_response_format="auto")
    report = settings.configuration()
    assert report["values"]["fleet_llm_response_format"] == "json_object"
    assert report["sources"]["fleet_llm_response_format"] == "toml"
    assert report["sources"]["fleet_sub_response_format"] == "dotenv"
    assert report["sources"]["fleet_root_response_format"] == "environment"
    assert report["sources"]["fleet_judge_response_format"] == "explicit"
    updated = settings.for_session({"models": {"root": "openai/other", "sub": "openai/child"}})
    assert updated.resolved_model("root").response_format == "auto"
    assert updated.resolved_model("reflection").response_format == "json_object"


@pytest.mark.parametrize("role", ["root", "sub", "reflection", "judge"])
def test_unity_gateway_native_wire_and_reconstruction_leave_openai_unchanged(role, monkeypatch):
    requests = []
    monkeypatch.setenv("GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("ORDINARY_KEY", "ordinary-key")
    model_field = "fleet_correctness_model" if role == "judge" else f"fleet_{role}_model"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers["Authorization"], body))
            payload = json.dumps(
                {
                    "id": "offline-gateway-response",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "46"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        settings = FleetSettings(
            _env_file=None,
            fleet_llm_model="openai/catalog.ai_gateway.qwen",
            fleet_llm_api_base=base + "/ai-gateway/mlflow/v1",
            fleet_llm_api_key="gateway-key",
            fleet_llm_max_tokens=37,
            fleet_llm_cache=False,
            **{
                model_field: "openai/catalog.ai_gateway.qwen",
                f"fleet_{role}_api_base": base + "/ai-gateway/mlflow/v1",
                f"fleet_{role}_api_key_env": "GATEWAY_KEY",
                f"fleet_{role}_max_tokens": 37,
                f"fleet_{role}_cache": False,
            },
        )
        gateway = build_lm(settings, "", role)
        assert complete_text(gateway, "17 + 29") == "46"
        # A second endpoint/credential must not replace the first LM's connection or dialect.
        ordinary = build_lm(
            FleetSettings(
                _env_file=None,
                fleet_llm_model="openai/ordinary-model",
                fleet_llm_api_base=base + "/v1",
                fleet_llm_api_key="ordinary-key",
                fleet_llm_max_tokens=53,
                fleet_llm_cache=False,
                **{
                    model_field: "openai/ordinary-model",
                    f"fleet_{role}_api_base": base + "/v1",
                    f"fleet_{role}_api_key_env": "ORDINARY_KEY",
                    f"fleet_{role}_max_tokens": 53,
                    f"fleet_{role}_cache": False,
                },
            ),
            "",
            role,
        )
        assert complete_text(ordinary, "17 + 29") == "46"
        other_gateway = build_lm(
            FleetSettings(
                _env_file=None,
                **{
                    model_field: "openai/second-service",
                    f"fleet_{role}_api_base": base.replace("127.0.0.1", "localhost") + "/ai-gateway/mlflow/v1/",
                    f"fleet_{role}_api_key_env": "ORDINARY_KEY",
                    f"fleet_{role}_max_tokens": 71,
                    f"fleet_{role}_cache": False,
                },
            ),
            "",
            role,
        )
        assert complete_text(other_gateway, "17 + 29") == "46"
        # Native saved LMs omit credentials; reconstruction rebinds one without a custom engine.
        state = json.loads(json.dumps(gateway.dump_state()))
        assert "gateway-key" not in json.dumps(state)
        restored = dspy.LM.load_state(state).copy(api_key="restored-key")
        assert complete_text(restored, "17 + 29") == "46"
        assert [path for path, _, _ in requests] == [
            "/ai-gateway/mlflow/v1/chat/completions",
            "/v1/chat/completions",
            "/ai-gateway/mlflow/v1/chat/completions",
            "/ai-gateway/mlflow/v1/chat/completions",
        ]
        assert [auth for _, auth, _ in requests] == [
            "Bearer gateway-key",
            "Bearer ordinary-key",
            "Bearer ordinary-key",
            "Bearer restored-key",
        ]
        for _, _, body in (requests[0], requests[3]):
            assert body["model"] == "catalog.ai_gateway.qwen"
            assert body["max_tokens"] == 37
            assert "max_completion_tokens" not in body
        assert requests[1][2]["max_completion_tokens"] == 53
        assert "max_tokens" not in requests[1][2]
        assert requests[2][2]["model"] == "second-service"
        assert requests[2][2]["max_tokens"] == 71
        assert "max_completion_tokens" not in requests[2][2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# Native DSPy runtime contracts


@pytest.mark.parametrize("timeout,expected_dispatches", [(0, 1), (0.001, 2)])
def test_native_batch_straggler_resubmission_is_disabled_by_zero(monkeypatch, timeout, expected_dispatches):
    import dspy.utils.parallelizer as parallelizer

    started, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    dispatches = []
    real_wait = parallelizer.wait
    waits = 0

    class Decision(dspy.Module):
        def forward(self, item):
            dispatches.append(item)
            if len(dispatches) == 1:
                started.set()
                assert release.wait(10)
            else:
                duplicate.set()
            return dspy.Prediction(answer=item)

    def wait(futures, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 1:
            assert started.wait(10)
            return set(), set(futures)
        if timeout:
            assert duplicate.wait(10)
        release.set()
        return real_wait(futures, **kwargs)

    ticks = iter([100, 200, 300, 400])
    monkeypatch.setattr(parallelizer, "time", SimpleNamespace(time=lambda: next(ticks, 500)))
    monkeypatch.setattr(parallelizer, "wait", wait)
    try:
        results = Decision().batch(
            [dspy.Example(item="slow").with_inputs("item")], num_threads=2, timeout=timeout, disable_progress_bar=True
        )
    finally:
        release.set()
    assert [p.answer for p in results] == ["slow"]
    assert dispatches == ["slow"] * expected_dispatches


def test_api_app_import_does_not_pull_the_optimizer():
    """The FastAPI app is the primary application; the offline optimizer must not load with it."""
    code = (
        "import sys, fleet_rlm.api.app\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('fleet_rlm.optimize'))\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_decision_outputs_stay_on_predict_predictors():
    """TypeSafe decision types belong on Predict; RLM and Flex outputs stay generative prose."""
    for name, field in FleetTask.output_fields.items():
        assert field.annotation is str, name
    decision = item_decision(["a", "b"])
    assert issubclass(decision.output_fields["decision"].annotation, BaseModel)


def manager():
    replies = []
    counts = []
    return SimpleNamespace(
        consume_budget=lambda n=1: counts.append(n),
        check_turn=lambda: None,
        emit=lambda *_args, **_kwargs: None,
        sub_lm=lambda prompt: [prompt.upper()],
        sub_adapter=None,
        replies=replies,
        counts=counts,
    )


@pytest.mark.parametrize("batched", [False, True])
def test_injected_query_enforces_local_budget_and_charges_global_once(batched):
    m = manager()
    rlm = dspy.RLM("task -> answer", max_llm_calls=2, sub_lm=SandboxLM(m.sub_lm, m.consume_budget))
    interpreter = DaytonaInterpreter(m)
    rlm._inject_execution_context(interpreter, rlm._prepare_execution_tools())
    name = "llm_query_batched" if batched else "llm_query"
    args = [["first", "second"]] if batched else ["first"]
    m.replies.append(interpreter._handle_host_call({"id": "first", "tool": name, "args": args}))
    if not batched:
        m.replies.append(interpreter._handle_host_call({"id": "second", "tool": name, "args": ["second"]}))
    assert sum(m.counts) == 2
    m.replies.append(interpreter._handle_host_call({"id": "exhausted", "tool": name, "args": args}))
    assert "error" in m.replies[-1]
    assert sum(m.counts) == 2
    assert m.replies[0]["value"] == (["FIRST", "SECOND"] if batched else "FIRST")


@pytest.mark.parametrize("batched", [False, True])
def test_query_fallback_keeps_global_accounting(batched):
    m = manager()
    interpreter = DaytonaInterpreter(m)
    m.replies.append(
        interpreter._handle_host_call(
            {
                "id": "one",
                "tool": "llm_query_batched" if batched else "llm_query",
                "args": [["a", "b"]] if batched else ["a"],
            }
        )
    )
    assert sum(m.counts) == (2 if batched else 1)
    assert "error" not in m.replies[-1]


def test_multiple_completions_reserve_each_distinct_budget_once():
    calls = []
    global_count = []
    local_count = []
    lm = SandboxLM(
        SandboxLM(lambda *_args, **kw: calls.append(kw) or ["answer"] * kw["n"], global_count.append),
        local_count.append,
    )
    assert lm("question", n=3) == ["answer"] * 3
    assert global_count == [3] and local_count == [3] and len(calls) == 1
    with pytest.raises(TypeError):
        lm("question", api_key="forbidden")
    assert global_count == [3]


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_native_child_promotion_belongs_to_successful_host_invocation(outcome):
    events = []
    invocations = []
    m = manager()
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"), shutdown=lambda: events.append("closed")
    )

    def call():
        invocations.append("predictor")
        child = DaytonaInterpreter(child_manager, owns_manager=True)
        try:
            if outcome == "failed":
                raise RuntimeError("predictor failed")
            return {"answer": "ok"}
        finally:
            child.shutdown()

    if outcome == "cancelled":

        def cancelled():
            raise RuntimeError("Turn cancelled")

        m.check_turn = cancelled
    interpreter = DaytonaInterpreter(m, tools={"predictor": call})
    m.replies.append(interpreter._handle_host_call({"id": "call", "tool": "predictor", "args": []}))
    if outcome == "cancelled":
        # The pre-tool fence answers before dispatch: no tool call, no child, error reply.
        assert invocations == []
        assert events == []
        assert "cancelled" in m.replies[-1]["error"]
    elif outcome == "failed":
        # The tool is already running when it fails; its finally block closes the child once.
        assert invocations == ["predictor"]
        assert events == ["closed"]
        assert "error" in m.replies[-1]
    else:
        assert invocations == ["predictor"]
        assert events == ["promoted", "closed"]
        assert "value" in m.replies[-1]


def test_unowned_explicit_child_keeps_promotion_with_its_caller():
    events = []
    m = manager()
    child_manager = SimpleNamespace(shutdown=lambda: events.append("closed"))

    def call():
        DaytonaInterpreter(child_manager, owns_manager=False).shutdown()
        return "explicit caller owns cleanup"

    interpreter = DaytonaInterpreter(m, tools={"spawn": call})
    assert interpreter._invoke_tool("spawn", [], {}) == "explicit caller owns cleanup"
    assert events == []


def test_decision_evidence_and_missing_evidence():
    choice = make_choice_type((("yes", "yes"), ("no", "no")))
    result = choice(value="yes", confidence=0.8, probabilities={"yes": 0.9, "no": 0.1})
    evidence = decision_evidence(result, SimpleNamespace(model="test-model"))
    assert evidence["probabilities"] == {"yes": 0.9, "no": 0.1}
    assert evidence["confidence"] == 0.8 and evidence["raw_choice"] == "yes"
    assert decision_evidence(None, SimpleNamespace())["confidence"] is None


def test_batch_decisions_keep_alignment_and_evidence(monkeypatch):
    import fleet_rlm.sandbox.interpreter as module

    choice = make_choice_type((("a", "a"), ("b", "b")))
    predictions = [
        SimpleNamespace(decision=choice(value=v, confidence=0.7, probabilities={"a": 0.7, "b": 0.3}))
        for v in ["a", "b"]
    ]

    def batch(examples, *, num_threads, timeout):
        assert len(examples) == 2 and num_threads == 2 and timeout == 0
        return predictions

    monkeypatch.setattr(module.dspy, "Predict", lambda _signature: SimpleNamespace(batch=batch))
    m = manager()
    m.jev_lm = None
    charged = []
    m.consume_budget = lambda count, *, category: charged.append((count, category))
    results = DaytonaInterpreter(m)._jev_decide_batched([["one", "two"], "choose", ["a", "b"]], {})
    assert charged == [(2, "other_decisions")]
    assert [(r["item"], r["decision"]) for r in results] == [("one", "a"), ("two", "b")]
    assert all(r["confidence"] == 0.7 and r["probabilities"] == {"a": 0.7, "b": 0.3} for r in results)


@pytest.mark.parametrize("backend", ["generative", "typesafe"])
@pytest.mark.parametrize(
    "context,mode,expected,reason",
    [
        (None, "hybrid", "direct", None),
        ("selected", "hybrid", "rlm", "selected_context"),
        (None, "rlm", "rlm", "session_mode"),
        # An operator-selected direct mode keeps the direct route even with a context selected;
        # the direct branch inlines the workspace instead of overriding the route.
        ("selected", "direct", "direct", "session_mode"),
        (None, "direct", "direct", "session_mode"),
        (None, "flex", "flex", "session_mode"),
    ],
)
def test_routing_evidence_distinguishes_backend_and_override(monkeypatch, backend, context, mode, expected, reason):
    events = []
    m = DaytonaSandboxManager("key", "org", "volume", "test")
    # The direct lane inlines the workspace, so it inspects files; keep this test off the provider.
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    m.event_callback = lambda payload: events.append((payload["event"], payload))
    root = dspy.LM("openai/test-root", engine="lm15", cache=False)
    lm = (
        type("TypeSafe", (), {"model": "typesafe:test-jev", "supports_decision_requests": True})()
        if backend == "typesafe"
        else None
    )
    engine = FleetRuntime(m, root, root, jev_lm=lm)
    engine.program.routing.predict = lambda **_kwargs: SimpleNamespace(
        route=TRIAGE_ROUTE(
            value="direct",
            confidence=0.8,
            probabilities={"direct": 0.8, "rlm": 0.2},
        )
    )
    engine.program.reasoning.direct = lambda **_kwargs: SimpleNamespace(answer="ok")
    engine.program.reasoning.rlm = lambda **_kwargs: SimpleNamespace(answer="ok")
    engine.program.reasoning.flex = lambda **_kwargs: SimpleNamespace(answer="ok")
    try:
        result = engine(workspace_manifest="", memory_summary="", task="greeting", context=context, execution_mode=mode)
        evidence = next(p["evidence"] for name, p in events if name == "triage_decision")
        assert result.route == expected and evidence["effective_route"] == expected
        assert evidence["override_reason"] == reason
        assert evidence["backend"] == backend
        assert evidence["confidence"] == (0.8 if mode == "hybrid" else None)
    finally:
        m.end_turn()


def test_routing_regression_cases_cover_each_effective_route():
    import json
    from collections import Counter
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "fixtures/triage_cases.json").read_text())
    assert Counter(case["route"] for case in cases) == {"direct": 6, "rlm": 12}
    assert len({case["id"] for case in cases}) == 18
    assert all(case["review_status"] == "developer_reviewed" and case["rationale"] for case in cases)


@pytest.mark.parametrize("finish", ["recovery", "extraction", "failure"])
def test_installed_rlm_lifecycle_promotes_only_a_valid_completed_invocation(finish):
    from dspy import CodeExecutionError, FinalOutput
    from dspy.primitives.facade import BOOTSTRAP

    events = []
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"),
        shutdown=lambda: events.append("closed"),
    )

    class ControlledInterpreter(DaytonaInterpreter):
        def start(self):
            return None

        def execute(self, code, variables=None):
            del variables
            if code in (BOOTSTRAP, "import dspy"):
                return None
            if code.splitlines()[-1] == "SUBMIT":
                return FinalOutput({"answer": 391})
            raise CodeExecutionError("deliberate repair probe")

    def factory():
        return ControlledInterpreter(child_manager, owns_manager=True)

    rlm = dspy.RLM("task: str -> answer: int", interpreter_factory=factory, max_iters=2 if finish == "recovery" else 1)
    actions = iter(["fail", "SUBMIT"])
    rlm.generate_action = lambda **_kwargs: dspy.Prediction(reasoning="", code=next(actions))

    def extract(**_kwargs):
        if finish == "failure":
            raise RuntimeError("extraction failed")
        return dspy.Prediction(answer=391)

    rlm.extract = extract
    parent = DaytonaInterpreter(manager(), tools={"nested": lambda: rlm(task="controlled invocation")})
    if finish == "failure":
        with pytest.raises(RuntimeError, match="extraction failed"):
            parent._invoke_tool("nested", [], {})
        assert events == ["closed"]
    else:
        result = parent._invoke_tool("nested", [], {})
        assert result.answer == 391
        assert events == ["promoted", "closed"]
        assert "deliberate repair probe" in result.trajectory[0]["output"]
        assert (result.final_reasoning == "Extract forced final output") == (finish == "extraction")


def test_figure_extraction_is_shared_by_submit_grounding_and_the_review_queue():
    from fleet_rlm.contracts import figure_grounded, figures

    found = figures("Revenue $1,250.50 over 42 orders (id=a12345, ratio 0.5, n=7)")
    assert found == {"1250.50": 1250.5, "42": 42.0, "0.5": 0.5}
    # A gold label keeps single-digit answers; the grounding filter above is unchanged.
    assert figures("The answer is 8 of 3", include_small=True) == {"8": 8.0, "3": 3.0}
    assert figures("The answer is 8") == {}
    # Exponent, leading-dot, negative-currency and compact-list literals are typed figures too.
    assert figures("1e6 and .5 and -$1,250 and 1.5e3 and 2.5e-3") == {
        "1e6": 1e6,
        ".5": 0.5,
        "-1250": -1250.0,
        "1.5e3": 1500.0,
        "2.5e-3": 0.0025,
    }
    assert figures("scores 10,20,30 and 1,234,567 and a1e6 v1.2 (1.2)") == {
        "10": 10.0,
        "20": 20.0,
        "30": 30.0,
        "1234567": 1234567.0,
        "1.2": 1.2,
    }
    assert figure_grounded("1e6", 1e6, {1_000_000.0}) and not figure_grounded("1e6", 1e6, {999_000.0})
    assert figure_grounded("1e-3", 0.001, {0.001}) and not figure_grounded("1e-3", 0.001, {0.0004})
    assert figure_grounded("1250.5", 1250.5, {1250.49}) and not figure_grounded("1250.5", 1250.5, {1250.0})
    # The interpreter imports the same helpers rather than a private copy.
    from fleet_rlm.sandbox import interpreter

    assert interpreter.figures is figures and interpreter.figure_grounded is figure_grounded


# Installed program compatibility

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_approval_evidence_builds_without_source_lockfile(tmp_path: Path, monkeypatch) -> None:
    """Approval evidence must not require uv.lock in the current working directory."""
    monkeypatch.chdir(tmp_path)
    assert not (Path.cwd() / "uv.lock").exists()

    evidence = artifacts.approval_evidence(FleetSettings(_env_file=None), "incumbent", scorer_version=SCORER_VERSION)
    assert evidence["evidence_format"] == "3"
    assert len(evidence["dependency_fingerprint"]) == 64
    assert "lockfile_digest" not in evidence
    assert LOCK_PATH.is_file()
    assert DEFAULT_DEFINITION.digest() == "3d0cd582c972090c18437dfba13358e055970af6de9407e46515e75bd49fa0e8"

    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    engine = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-state"),
        lm,
        lm,
    )
    engine.program.routing.predict.signature = engine.program.routing.predict.signature.with_instructions("TUNED ROUTE")
    current = tmp_path / "fleet-program.json"
    current.write_bytes(program_envelope(engine.program))

    restored = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-fresh"), lm, lm, compiled_path=str(current)
    )
    assert restored.program.dump_state() == engine.program.dump_state()
    assert restored.program.routing.predict.signature.instructions == "TUNED ROUTE"


def test_dependency_fingerprint_changes_when_installed_version_changes(monkeypatch) -> None:
    """Approval identity includes distribution versions, not only package names."""
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.3")],
    )
    original = artifacts.installed_dependency_fingerprint()
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.4")],
    )
    assert artifacts.installed_dependency_fingerprint() != original


# Optimizer CLI contracts


def test_worker_snapshot_retains_effective_settings_and_sources_without_rereading_configuration(monkeypatch, tmp_path):
    settings = FleetSettings(_env_file=None, databricks_token="private-worker-token").with_explicit_overrides(
        turn_timeout_seconds=123
    )
    snapshot = worker_settings_payload(settings)
    assert b"private-worker-token" in snapshot
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "not-a-config.toml"))
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "invalid-env-must-not-be-read")
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert received.configuration() == settings.configuration()
    assert received.databricks_token.get_secret_value() == "private-worker-token"
    assert received.configuration_source("turn_timeout_seconds") == "explicit"
    assert "private-worker-token" not in json.dumps(received.configuration())


def test_worker_snapshot_rejects_unbounded_or_invalid_input():
    with pytest.raises(ValueError, match="too large"):
        read_worker_settings(io.StringIO(" " * 1_000_001))
    with pytest.raises(ValueError, match="invalid worker"):
        read_worker_settings(io.StringIO('{"settings":{}}'))


def test_worker_snapshot_ignores_unrelated_dotenv_keys(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER_APP_SECRET=unrelated-secret\nFLEET_TURN_TIMEOUT_SECONDS=123\n")
    settings = FleetSettings(_env_file=dotenv)
    assert "other_app_secret" not in settings._configuration_sources
    snapshot = worker_settings_payload(settings)
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert b"other_app_secret" not in snapshot and b"unrelated-secret" not in snapshot
    assert received.configuration() == settings.configuration()
    assert received.configuration_source("turn_timeout_seconds") == "dotenv"


@pytest.mark.parametrize("ratio", [0.1, 0.5])
def test_ratio_bounds_agree_across_entry_points(ratio, monkeypatch):
    from fleet_rlm.api.schemas import StartJob
    from fleet_rlm.optimize import dataset
    from fleet_rlm.tui.commands import Context, SessionState, _parse_optimizer_arguments

    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    options = ExperimentOptions.from_namespace(parser.parse_args(["--validation-ratio", str(ratio)]))
    assert options.validation_ratio == ratio
    assert StartJob(request_id="request", validation_ratio=ratio).validation_ratio == ratio
    payload, review = _parse_optimizer_arguments(Context(None, "s1", SessionState()), f"--validation-ratio {ratio}")
    assert payload["validation_ratio"] == ratio and review is None
    monkeypatch.setattr(dataset, "load_examples_from_postgres", lambda *_args: [])
    monkeypatch.setattr(dataset, "load_examples_from_mlflow", lambda *_args, **_kwargs: [])
    with pytest.raises(ValueError, match="approved gold-labeled examples"):
        dataset.curate_dataset(None, "offline", validation_ratio=ratio)


@pytest.mark.parametrize("ratio", [0, 0.09, 0.51, 1, float("nan"), float("inf")])
def test_invalid_ratio_rejected_by_shared_and_direct_contract(ratio):
    from fleet_rlm.optimize.dataset import curate_dataset

    with pytest.raises(ValidationError):
        ExperimentOptions(validation_ratio=ratio)
    with pytest.raises(ValueError, match="validation_ratio"):
        curate_dataset(None, "offline", validation_ratio=ratio)


def test_cli_and_http_search_controls_have_one_validated_contract():
    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    args = parser.parse_args(["--max-metric-calls", "10", "--trace-search", "--max-total-tokens", "5000"])
    options = ExperimentOptions.from_namespace(args)
    assert options.gepa_budget is None and options.max_metric_calls == 10
    assert options.trace_search and options.max_total_tokens == 5000
    assert "--max-total-tokens" in options.cli_arguments() and "--check" not in options.cli_arguments()
    assert ExperimentOptions().max_total_tokens is None and ExperimentOptions(max_total_tokens=0).max_total_tokens == 0
    assert ExperimentOptions.model_validate(options.model_dump()) == options
    assert "--max-metric-calls" in options.cli_arguments() and "--gepa-budget" not in options.cli_arguments()
    with pytest.raises(ValidationError):
        ExperimentOptions(max_metric_calls=10, gepa_budget="light")
    with pytest.raises(ValidationError):
        ExperimentOptions(max_total_tokens=-1)
    with pytest.raises(ValidationError):
        ExperimentOptions(threads=True)
    with pytest.raises(ValidationError):
        ExperimentOptions.model_validate({"executable": "untrusted"})


def test_worker_preserves_referenced_credentials_and_role_defaults(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text("[config]\nschema_version=5\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    path = tmp_path / ".env"
    path.write_text("CUSTOM_PROVIDER_KEY=original-key\n")
    settings = FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_PROVIDER_KEY", fleet_llm_temperature=0.4)
    payload = worker_settings_payload(settings)
    path.write_text("CUSTOM_PROVIDER_KEY=replaced-key\n")
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "environment-replacement")
    received = read_worker_settings(io.StringIO(payload.decode()))
    assert received.resolved_model("root").api_key.get_secret_value() == "original-key"
    assert received.resolved_model("reflection").temperature == 0.4
    assert received.configuration()["digest"] == settings.configuration()["digest"]
    updated = received.with_explicit_overrides(fleet_root_cache=False)
    assert updated.resolved_model("root").api_key.get_secret_value() == "original-key"
    assert updated.resolved_model("reflection").temperature == 0.4
    assert "original-key" not in str(received.configuration())
