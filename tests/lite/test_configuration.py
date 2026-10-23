"""Configuration source precedence, provenance, and secret boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fleet_rlm.config import FleetSettings, resolve_turn_deadline


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # These tests exercise .env precedence from a private tmp_path working directory.
    monkeypatch.setitem(FleetSettings.model_config, "env_file", ".env")


def toml(text):
    Path("config").mkdir(exist_ok=True)
    Path("config/fleet.toml").write_text("[config]\nschema_version = 4\n" + text)


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


def test_optional_default_file_and_explicit_path(monkeypatch, tmp_path):
    assert FleetSettings(_env_file=None).configuration()["sources"]["execution_mode"] == "default"
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(ValueError, match="existing TOML"):
        FleetSettings(_env_file=None)
    p = tmp_path / "selected.toml"
    p.write_text('[config]\nschema_version=4\n[runtime]\nexecution_mode="rlm"')
    monkeypatch.setenv("FLEET_CONFIG", str(p))
    assert FleetSettings(_env_file=None).execution_mode == "rlm"


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


@pytest.mark.parametrize("value", [0, 10, 3600])
def test_turn_timeout_accepts_zero_or_bounded_integers(value):
    assert FleetSettings(_env_file=None, turn_timeout_seconds=value).turn_timeout_seconds == value


@pytest.mark.parametrize("value", [9, -1, True, 0.5, 3601, "600"])
def test_turn_timeout_rejects_other_shapes(value):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, turn_timeout_seconds=value)
    message = str(error.value)
    assert "turn_timeout_seconds" in message
    assert "0 or between 10 and 3600" in message


def test_turn_timeout_environment_text_and_zero_agree_with_toml(monkeypatch):
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "0")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "600")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600
    toml("[runtime]\nturn_timeout_seconds = 0")
    monkeypatch.delenv("FLEET_TURN_TIMEOUT_SECONDS")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0


@pytest.mark.parametrize("value", ["true", "0.5", '"600"', "-1", "9", "3601"])
def test_toml_turn_timeout_stays_a_bounded_integer(value):
    toml(f"[runtime]\nturn_timeout_seconds = {value}")
    with pytest.raises(ValueError):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize(
    ("configured", "configured_source", "requested", "expected"),
    [
        (900, "default", None, ("default", 900)),
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


def test_schema_and_parse_errors(monkeypatch):
    monkeypatch.setenv("FLEET_CONFIG", "file.toml")
    for text in ("x=[", "[config]\nschema_version=2", "[config]\nschema_version=4\nextra=1"):
        Path("file.toml").write_text(text)
        with pytest.raises(ValueError):
            FleetSettings(_env_file=None)


def test_role_settings_are_passed_without_provider_controlled_options(monkeypatch):
    toml("[llm.root]\nmax_tokens=16384\ntemperature=0.0\nnum_retries=1\ncache=true\n[llm.sub]\nmax_tokens=8192\n")
    calls = []
    monkeypatch.setattr("fleet_rlm.config.dspy.LM", lambda model, **kwargs: calls.append((model, kwargs)))
    FleetSettings(
        _env_file=None, databricks_host="https://example.invalid", databricks_token="credential-marker"
    ).build_lms()
    assert calls[0][1]["max_tokens"] == 16384
    assert calls[0][1]["temperature"] == 0
    assert calls[0][1]["cache"] is True and calls[0][1]["num_retries"] == 1
    assert calls[1][1]["max_tokens"] == 8192
    assert "temperature" not in calls[1][1]
    assert "max_tokens" not in calls[2][1]
    assert all(kwargs["engine"] == "lm15" for _, kwargs in calls)


def test_build_jev_lm_returns_none_without_typesafe_key():
    assert FleetSettings(_env_file=None, typesafe_api_key="").build_jev_lm() is None


def test_build_jev_lm_builds_the_configured_typesafe_model(monkeypatch):
    import dspy.experimental

    built = []

    class StubTypeSafe:
        def __init__(self, model, *, api_key):
            built.append((model, api_key))

    monkeypatch.setattr(dspy.experimental, "TypeSafe", StubTypeSafe)
    lm = FleetSettings(_env_file=None, typesafe_api_key="credential-marker", typesafe_model="jev-1.14.0").build_jev_lm()
    assert isinstance(lm, StubTypeSafe)
    assert built == [("jev-1.14.0", "credential-marker")]


def test_shipped_defaults_cap_root_output_but_not_sub(monkeypatch):
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    calls = []
    monkeypatch.setattr("fleet_rlm.config.dspy.LM", lambda model, **kwargs: calls.append((model, kwargs)))
    FleetSettings(
        _env_file=None, databricks_host="https://example.invalid", databricks_token="credential-marker"
    ).build_lms()
    assert calls[0][1]["max_tokens"] == 16384
    assert "max_tokens" not in calls[1][1]


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


def test_validation_errors_hide_input_values():
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, fleet_root_temperature="credential-marker")
    assert "credential-marker" not in str(error.value)


@pytest.mark.parametrize("field", ["databricks_host", "fleet_llm_base_url", "mlflow_databricks_tracking_uri"])
def test_public_urls_cannot_expose_credentials(field):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, **{field: "https://user:credential-marker@example.invalid"})
    assert "credential-marker" not in str(error.value)


def test_explicit_cli_copy_is_validated_and_does_not_mutate_original():
    toml("[server]\nport=8001\n")
    settings = FleetSettings(_env_file=None)
    updated = settings.with_explicit_overrides(port=8002)
    assert updated.port == 8002 and settings.port == 8001
    assert updated.configuration()["sources"]["port"] == "explicit"
    assert settings.configuration()["sources"]["port"] == "toml"
    with pytest.raises(ValidationError):
        settings.with_explicit_overrides(port=0)


def test_configuration_is_frozen_until_new_settings_are_loaded():
    toml("[runtime]\nturn_timeout_seconds=900\n")
    settings = FleetSettings(_env_file=None)
    toml("[runtime]\nturn_timeout_seconds=600\n")
    assert settings.turn_timeout_seconds == 900
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600


def test_schema_version_must_be_integer():
    Path("config").mkdir()
    Path("config/fleet.toml").write_text("[config]\nschema_version=3.0\n")
    with pytest.raises(ValueError, match="schema_version"):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize(
    "values",
    [
        {"fleet_root_model": "unsupported/model"},
        {"fleet_sub_model": "openai/"},
        {"fleet_llm_base_url": "http://example.invalid"},
    ],
)
def test_invalid_routes_fail_during_settings_loading(values):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("FLEET_ROOT_MODEL", "system.ai.deepseek-v4-1-flash", "fleet_root_model"),
        ("FLEET_LLM_BASE_URL", "http://example.invalid", "fleet_llm_base_url"),
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


def test_local_mlflow_mode_ignores_databricks_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "databricks://profile")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME", "/Users/someone/fleet")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert target == ("local", "http://127.0.0.1:5001", "fleet-rlm-dev", "fleet-rlm-eval", "fleet-rlm-gepa")


def test_local_mlflow_mode_keeps_local_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "mine")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert (target.tracking_uri, target.experiment_name) == ("http://127.0.0.1:5002", "mine")


def test_databricks_mlflow_mode_is_validated_only_when_selected(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", "")
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    with pytest.raises(ValidationError, match="Unity Catalog"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "http://127.0.0.1:5001")
    with pytest.raises(ValidationError, match="databricks tracking URI"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")


def test_configuration_defaults_to_local_and_keeps_databricks_available():
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    target = FleetSettings(_env_file=None, mlflow_mode="databricks").mlflow_target()
    assert target.mode == "databricks"
    assert target.experiment_name.startswith("/")
    assert target.optimization_experiment_name.startswith("/")


def test_gateway_judge_environment_uses_gateway_credentials():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_base_url="https://workspace.example/ai-gateway/mlflow/v1",
        databricks_token="token",
    )
    assert settings.fleet_judge_model == "openai:/system.ai.deepseek-v4-1-flash"
    assert settings.judge_environment() == {
        "OPENAI_API_KEY": "token",
        "OPENAI_API_BASE": "https://workspace.example/ai-gateway/mlflow/v1",
    }
    assert FleetSettings(_env_file=None, fleet_judge_model="databricks:/endpoint").judge_environment() == {}
    with pytest.raises(ValidationError, match="provider"):
        FleetSettings(_env_file=None, fleet_judge_model="deepseek")


def test_gateway_discovery_filters_chat_services_and_converts_resource_names(monkeypatch) -> None:
    from databricks import sdk
    from databricks.sdk import core

    monkeypatch.setattr(core, "Config", lambda **kwargs: SimpleNamespace(**kwargs))

    def list_services(**kwargs):
        assert kwargs == {"parent": "schemas/team.chat"}
        return [
            SimpleNamespace(name="model-services/team.chat.second", supported_api_types=["mlflow/v1/chat/completions"]),
            SimpleNamespace(name="model-services/team.chat.embedding", supported_api_types=["mlflow/v1/embeddings"]),
            SimpleNamespace(name="model-services/team.chat.first", supported_api_types=["mlflow/v1/chat/completions"]),
        ]

    monkeypatch.setattr(
        sdk,
        "WorkspaceClient",
        lambda **_kwargs: SimpleNamespace(ai_gateway=SimpleNamespace(list_model_services=list_services)),
    )
    settings = FleetSettings(databricks_host="https://example.databricks.com", databricks_token="test-only")
    assert settings.list_models("team.chat") == ["openai/team.chat.first", "openai/team.chat.second"]
    with pytest.raises(ValueError, match=r"catalog\.schema"):
        settings.list_models("../other")


def test_checked_in_toml_loads_without_snapshot_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    settings = FleetSettings(_env_file=None)
    assert not hasattr(settings, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in settings.configuration()["values"]
    assert "snapshot" not in settings.configuration()["values"]


def test_leftover_toml_snapshot_key_raises_unsupported_setting() -> None:
    toml('[daytona]\nsnapshot = "fleet-rlm-python313-v10"\n')
    with pytest.raises(ValueError, match=r"Unsupported or environment-only Fleet TOML setting: daytona\.snapshot"):
        FleetSettings(_env_file=None)


def test_snapshot_environment_variable_creates_no_field_or_digest_change(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    baseline = FleetSettings(_env_file=None)
    monkeypatch.setenv("FLEET_DAYTONA_SNAPSHOT", "fleet-rlm-base")
    overridden = FleetSettings(_env_file=None)
    assert not hasattr(overridden, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in overridden.configuration()["values"]
    assert overridden.configuration()["digest"] == baseline.configuration()["digest"]
