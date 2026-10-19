"""Backend selection, safe reuse, and subprocess isolation without external services."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from fleet_rlm import cli, launcher
from fleet_rlm.api import app as api
from fleet_rlm.config import FleetSettings
from fleet_rlm.tui import commands


def test_profiles_have_independent_precedence_and_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fleet.toml").write_text(
        '[config]\nschema_version=4\n[mlflow.local]\nexperiment_name="local-toml"\n'
        '[mlflow.databricks]\nexperiment_name="/Shared/db-toml"\n'
    )
    (tmp_path / ".env").write_text("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME=local-dotenv\n")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME", "/Shared/db-env")
    local = FleetSettings(_env_file=".env")
    db = FleetSettings(_env_file=".env", mlflow_mode="databricks")
    assert local.mlflow_experiment_name == "local-dotenv"
    assert db.mlflow_experiment_name == "/Shared/db-env"
    assert local.configuration()["sources"]["mlflow_local_experiment_name"] == "dotenv"
    assert db.configuration()["sources"]["mlflow_databricks_experiment_name"] == "environment"
    assert local.configuration()["digest"] != db.configuration()["digest"]
    assert local.with_explicit_overrides(mlflow_local_experiment_name="explicit").mlflow_experiment_name == "explicit"


@pytest.mark.parametrize("legacy", ["FLEET_MLFLOW_MODE", "FLEET_MLFLOW_EXPERIMENT_NAME", "MLFLOW_TRACKING_URI"])
def test_retired_dotenv_keys_require_migration_without_showing_values(tmp_path, monkeypatch, legacy):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{legacy}=private-marker\n")
    with pytest.raises(ValueError, match="migrate") as error:
        FleetSettings(_env_file=".env")
    assert legacy in str(error.value)
    assert "private-marker" not in str(error.value)


def test_retired_process_mode_cannot_redirect_the_cli(monkeypatch, capsys):
    monkeypatch.setenv("FLEET_MLFLOW_MODE", "databricks")
    with pytest.raises(SystemExit) as error:
        cli.main(["cli"])
    assert error.value.code == 2
    assert "migrate" in capsys.readouterr().err


def test_old_schema_and_flat_mlflow_settings_require_migration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "fleet.toml"
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    path.write_text("[config]\nschema_version=3\n")
    with pytest.raises(ValueError, match="migrate"):
        FleetSettings(_env_file=None)
    path.write_text('[config]\nschema_version=4\n[mlflow]\nmode="databricks"\n')
    with pytest.raises(ValueError, match=r"mlflow\.mode"):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize("arguments", [["databricks"], ["--mlflow", "databricks"], ["--session", "s1", "databricks"]])
def test_cli_selects_databricks_and_propagates_the_profile(monkeypatch, arguments):
    from contextlib import nullcontext

    seen = {}
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.invalid")
    monkeypatch.setattr(launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    monkeypatch.setattr(launcher, "running_services", lambda plan, **_kwargs: nullcontext(plan))
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **kwargs: seen.update(kwargs))
    cli.main(["cli", *arguments])
    assert seen["settings"].mlflow_mode == "databricks"
    assert seen["settings"].mlflow_target().experiment_name.startswith("/")


@pytest.mark.parametrize(
    "arguments",
    [
        ["cli", "databricks", "--mlflow", "local"],
        ["cli", "databricks", "--mlflow-port", "5002"],
        ["cli", "--mlflow", "env"],
        ["cli", "--mlflow-port", "0"],
    ],
)
def test_invalid_selectors_and_overrides_fail_before_launch(monkeypatch, arguments):
    monkeypatch.setattr(launcher, "plan_launch", lambda **_kwargs: pytest.fail("must not launch"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_standalone_commands_receive_selected_backend(monkeypatch, backend):
    seen = []
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_eval_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    monkeypatch.setattr(cli, "_trace_verify_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    monkeypatch.setattr(
        "fleet_rlm.optimize.gepa.run", lambda _args, _parser, settings: seen.append(settings.mlflow_mode)
    )
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **_kwargs: seen.append(app.state.settings.mlflow_mode))
    cli.main(["eval", "label", "tr-1", "--mlflow", backend])
    cli.main(["trace", "verify", "tr-1", "--mlflow", backend])
    cli.main(["optimize", "--session", "s1", "--mlflow", backend])
    cli.main(["serve", "--mlflow", backend])
    assert seen == [backend] * 4


def test_standalone_local_command_explains_missing_server(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: False)
    with pytest.raises(SystemExit) as error:
        cli.main(["trace", "verify", "tr-1"])
    assert error.value.code == 2
    assert "Local MLflow is not reachable" in capsys.readouterr().err


def _health(settings):
    return {"status": "ok", "mlflow": {**settings.mlflow_descriptor(), "ready": True, "experiment_id": "17"}}


def _respond(monkeypatch, payload):
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://fleet.test/health")
        ),
    )


def test_reuse_checks_metadata_without_creating_a_session(monkeypatch):
    settings = FleetSettings(_env_file=None)
    _respond(monkeypatch, _health(settings))
    plan = launcher.plan_launch(url="http://fleet.test", host="127.0.0.1", port=8000, settings=settings)
    assert not plan.start_api and not plan.start_mlflow


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "databricks"),
        ("tracking_uri", "http://127.0.0.1:5002"),
        ("experiment_name", "other"),
        ("eval_experiment_name", "other"),
        ("optimization_experiment_name", "other"),
        ("trace_location", {"catalog": "wrong"}),
    ],
)
def test_reuse_rejects_destination_mismatches(monkeypatch, field, value):
    settings = FleetSettings(_env_file=None)
    payload = _health(settings)
    payload["mlflow"][field] = value
    _respond(monkeypatch, payload)
    with pytest.raises(launcher.LaunchError, match="destination mismatch"):
        launcher.verify_api_destination("http://fleet.test", settings.mlflow_descriptor())


@pytest.mark.parametrize("payload", [[], {"status": "ok"}, {"status": "ok", "mlflow": {"ready": False}}])
def test_reuse_rejects_unknown_or_unready_servers(monkeypatch, payload):
    _respond(monkeypatch, payload)
    with pytest.raises(launcher.LaunchError, match="no ready MLflow"):
        launcher.verify_api_destination("http://fleet.test", FleetSettings(_env_file=None).mlflow_descriptor())


def test_eval_preflight_rejects_runtime_experiment_before_work(monkeypatch):
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks", databricks_host="https://workspace.invalid")
    _respond(monkeypatch, _health(settings))
    monkeypatch.setattr(
        "mlflow.set_experiment", lambda *_args, **_kwargs: pytest.fail("must not create evaluation work")
    )
    with pytest.raises(SystemExit, match="destination mismatch"):
        cli._eval_command(SimpleNamespace(eval_command="run", url="http://fleet.test"), settings)


def test_databricks_health_rejects_another_workspace_before_evaluation(monkeypatch):
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks", databricks_host="https://client.invalid")
    server_settings = settings.with_explicit_overrides(databricks_host="https://server.invalid")
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: None)
    with TestClient(api.create_app(settings=server_settings, initialize_db=False)) as client:
        health = client.get("/health").json()
    assert health["mlflow"]["workspace_host"] == "https://server.invalid"
    _respond(monkeypatch, health)
    with pytest.raises(launcher.LaunchError, match="destination mismatch"):
        launcher.verify_api_destination("http://fleet.test", settings.mlflow_descriptor())


@pytest.mark.parametrize("tracking_uri", ["databricks", "databricks://named"])
def test_databricks_workspace_identity_uses_resolved_profile_host(monkeypatch, tracking_uri):
    calls = []

    def credentials(uri):
        calls.append(uri)
        return SimpleNamespace(host="https://PROFILE.invalid/", token="private-marker")

    monkeypatch.setattr("mlflow.utils.databricks_utils.get_databricks_host_creds", credentials)
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri=tracking_uri,
        databricks_host="https://gateway.invalid" if tracking_uri != "databricks" else "",
    )
    descriptor = settings.mlflow_descriptor(evaluation=True)
    assert descriptor["workspace_host"] == "https://profile.invalid"
    assert calls == [tracking_uri]
    assert "private-marker" not in str(descriptor)


def test_databricks_workspace_identity_resolution_fails_closed(monkeypatch):
    def fail(_uri):
        raise ValueError("private-marker")

    monkeypatch.setattr("mlflow.utils.databricks_utils.get_databricks_host_creds", fail)
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks", databricks_host="")
    with pytest.raises(ValueError, match="Could not resolve") as error:
        settings.mlflow_descriptor()
    assert "private-marker" not in str(error.value)


def test_tracing_failure_releases_owned_database_pool(monkeypatch):
    events = []
    store = SimpleNamespace(
        initialize=lambda: events.append("initialize"),
        close_migration_pool=lambda: events.append("close migration"),
        close=lambda: events.append("close store"),
    )
    monkeypatch.setattr(api.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(api, "mlflow_is_healthy", lambda _url: False)
    with (
        pytest.raises(RuntimeError, match="Local MLflow is not reachable"),
        TestClient(api.create_app(settings=FleetSettings(_env_file=None))),
    ):
        pytest.fail("application must not become ready")
    assert events == ["initialize", "close migration", "close store"]


@pytest.mark.parametrize("failure", ["registry", "store", "flush"])
def test_shutdown_attempts_every_owned_cleanup_after_a_failure(monkeypatch, failure):
    events = []

    def cleanup(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(f"{name} cleanup failed")

    store = SimpleNamespace(initialize=lambda: None, close_migration_pool=lambda: None, close=lambda: cleanup("store"))
    monkeypatch.setattr(api.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(
        api, "SessionRuntimeRegistry", lambda *_args: SimpleNamespace(close=lambda: cleanup("registry"))
    )
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: cleanup("flush"))
    app = api.create_app(settings=FleetSettings(_env_file=None))
    if failure == "flush":
        with TestClient(app):
            pass
    else:
        with pytest.raises(RuntimeError, match=f"{failure} cleanup failed"), TestClient(app):
            pass
    assert events == ["registry", "store", "flush"]


def test_shutdown_preserves_injected_store_ownership(monkeypatch):
    store = SimpleNamespace(close=lambda: pytest.fail("injected Store belongs to its caller"))
    monkeypatch.setattr(api, "SessionRuntimeRegistry", lambda *_args: SimpleNamespace(close=lambda: None))
    with TestClient(
        api.create_app(
            settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False, store=store
        )
    ):
        pass


def test_health_reports_successfully_initialized_tracking_without_a_session(monkeypatch):
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: None)
    app = api.create_app(settings=FleetSettings(_env_file=None), initialize_db=False)
    with TestClient(app) as client:
        payload = client.get("/health").json()
        assert payload == {**_health(app.state.settings), "version": api.VERSION}


def test_databricks_initialization_errors_abort_startup_without_echoing_credentials(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("private-marker")

    monkeypatch.setattr(api, "configure_mlflow_observability", fail)
    with pytest.raises(RuntimeError, match="Could not initialize databricks") as error:
        api._configure_tracing(FleetSettings(_env_file=None, mlflow_mode="databricks"))
    assert "private-marker" not in str(error.value)


def test_databricks_auth_uses_dotenv_credentials_but_named_profile_stays_explicit(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATABRICKS_HOST=https://workspace.invalid\nDATABRICKS_TOKEN=private-marker\n")
    settings = FleetSettings(_env_file=path, mlflow_mode="databricks")
    monkeypatch.setenv("DATABRICKS_HOST", "")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    settings.prepare_mlflow_auth()
    import os

    assert os.environ["DATABRICKS_TOKEN"] == "private-marker"
    assert os.environ["DATABRICKS_HOST"] == "https://workspace.invalid"
    monkeypatch.setenv("DATABRICKS_TOKEN", "existing")
    settings.with_explicit_overrides(mlflow_databricks_tracking_uri="databricks://named").prepare_mlflow_auth()
    assert os.environ["DATABRICKS_TOKEN"] == "existing"
    assert "private-marker" not in str(settings.configuration())


def test_local_service_uses_configured_persistent_directory(tmp_path):
    data = tmp_path / "tracking"
    process = launcher._mlflow_process(5012, str(data))
    assert f"sqlite:///{data / 'mlflow.db'}" in process.command
    assert str(data / "artifacts") in process.command
    assert process.command[-2:] == ["--port", "5012"]


def test_optimize_inherits_backend_and_effective_profile_without_sdk_destination(monkeypatch, capsys):
    settings = FleetSettings(
        _env_file=None, mlflow_mode="databricks", mlflow_databricks_experiment_name="/Shared/custom"
    )
    state = commands.SessionState(mlflow_settings=settings)
    ctx = SimpleNamespace(session_id="s1", state=state)
    calls = []
    monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "stale")
    monkeypatch.setattr(commands.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    commands._optimize(ctx, "--max-metric-calls 1")
    command, kwargs = calls[0]
    assert command[-2:] == ["--mlflow", "databricks"]
    assert kwargs["env"]["FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME"] == "/Shared/custom"
    assert "MLFLOW_EXPERIMENT_ID" not in kwargs["env"]
    assert "MLFLOW_TRACKING_URI" not in kwargs["env"]
    commands._optimize(ctx, "--mlflow=local")
    assert len(calls) == 1
    assert "session's MLflow backend" in capsys.readouterr().out
