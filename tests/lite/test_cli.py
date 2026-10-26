from __future__ import annotations

import builtins
import io
import sys
import types
from types import SimpleNamespace

import pytest

from fleet_rlm import cli
from fleet_rlm.config import FleetSettings


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_serve_rejects_invalid_explicit_ports_before_starting_a_server(monkeypatch, capsys, port):
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *_args, **_kwargs: pytest.fail("invalid settings must not start a server")
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["serve", "--port", str(port)])
    assert error.value.code == 2
    assert "invalid configuration" in capsys.readouterr().err


def test_serve_validates_and_applies_explicit_bind_settings(monkeypatch):
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs))
    cli.main(["serve", "--host", "localhost", "--port", "12345"])
    assert (captured["host"], captured["port"]) == ("localhost", 12345)
    assert captured["app"].state.settings.configuration()["sources"]["port"] == "explicit"


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_eval_missing_experiment_diagnostic_names_selected_profile(monkeypatch, backend):
    import mlflow

    from fleet_rlm.config import MlflowTarget

    # Validated settings reject empty names; exercise the defensive boundary with a double.
    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget(backend, "offline", "runtime", "", "opt"),
        mlflow_descriptor=lambda **_kwargs: {},
        mlflow_tracing_sql_warehouse_id="offline",
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)
    with pytest.raises(SystemExit) as error:
        cli._eval_command(SimpleNamespace(eval_command="run", url="http://test"), settings)
    message = str(error.value)
    assert f"FLEET_MLFLOW_{backend.upper()}_EVAL_EXPERIMENT_NAME" in message
    assert "FLEET_MLFLOW_EVAL_EXPERIMENT_NAME" not in message


@pytest.mark.parametrize("backend", ["local", "databricks"])
def test_eval_rejects_empty_selected_experiment_before_external_work(monkeypatch, capsys, backend):
    monkeypatch.setenv(f"FLEET_MLFLOW_{backend.upper()}_EVAL_EXPERIMENT_NAME", "")
    monkeypatch.setattr(
        cli.launcher, "mlflow_is_healthy", lambda *_args: pytest.fail("invalid settings must not probe MLflow")
    )
    monkeypatch.setattr(cli, "_eval_command", lambda *_args: pytest.fail("invalid settings must not evaluate"))
    with pytest.raises(SystemExit) as error:
        cli.main(["eval", "dataset", "--mlflow", backend])
    assert error.value.code == 2
    assert "invalid configuration" in capsys.readouterr().err


def test_eval_run_passes_destination_and_exits_unsuccessfully_on_incomplete_collection(monkeypatch, capsys):
    import mlflow
    import pytest

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.optimize import evaluation

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        judge_environment=lambda: {"OPENAI_API_BASE": "https://gateway.example/v1", "OPENAI_API_KEY": "gateway-key"},
        fleet_judge_model="databricks:/judge",
        build_jev_lm=lambda: None,
        mlflow_descriptor=lambda **_kwargs: {},
    )
    monkeypatch.setenv("OPENAI_API_BASE", "https://stale-provider.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-key")
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda _name: SimpleNamespace(experiment_id="eval-id"))
    captured = {}
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)

    def evaluate(_cases, **kwargs):
        import os

        assert os.environ["OPENAI_API_BASE"] == "https://gateway.example/v1"
        assert os.environ["OPENAI_API_KEY"] == "gateway-key"
        captured.update(kwargs)
        kwargs["report"]("Case direct-00: trace tr-1")
        raise evaluation.EvaluationError("Expectations are not readable yet; recover trace tr-1 without rerunning")

    monkeypatch.setattr(evaluation, "evaluate_cases", evaluate)
    with pytest.raises(SystemExit, match="recover trace tr-1"):
        cli._eval_command(
            SimpleNamespace(eval_command="run", seed=7, limit=1, url="http://test", collect_only=True),
            settings,
        )
    assert captured["experiment_id"] == "eval-id"
    assert captured["uc_location"] is None
    assert captured["jev_lm"] is None
    assert "Collected labelled Turns" not in capsys.readouterr().out


def test_eval_label_cli_accepts_critical_and_explicit_unpin(monkeypatch) -> None:
    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_eval_command", lambda args, _settings: captured.append(args))

    cli.main(["eval", "label", "tr-1", "--value", "42", "--critical"])
    cli.main(["eval", "label", "tr-1", "--value", "42", "--no-critical"])

    assert [args.critical for args in captured] == [True, False]


def test_eval_label_syncs_selected_replay_labels_to_configured_lakebase(monkeypatch) -> None:
    import mlflow

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.store import Store

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        fleet_database_url="postgresql://example",
        fleet_lakebase_endpoint=None,
        fleet_database_schema="fleet_rlm_lite",
    )
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_metadata={"mlflow.trace.session": "session-1"}),
        data=SimpleNamespace(
            request={
                "replay_snapshot_selected": True,
                "session_id": "session-1",
                "turn_id": "turn-1",
                "replay_snapshot_sha256": "a" * 64,
                "task": "Review this source file",
                "workspace_manifest": "/workspace/files/input.txt",
                "context_files": {"files/input.txt": "b" * 64},
            }
        ),
    )
    trace.search_assessments = lambda **_kwargs: []
    monkeypatch.setattr("fleet_rlm.replay.selected_snapshot", lambda *_args: {"task": "immutable source"})
    calls = {}
    store = SimpleNamespace(
        save_review_labels=lambda *args, **kwargs: calls.update(label_args=args, labels=kwargs),
        close=lambda: calls.update(closed=True),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_trace", lambda *_args, **_kwargs: trace)
    monkeypatch.setattr(Store, "open", lambda *args, **kwargs: calls.update(open=(args, kwargs)) or store)
    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_trace", lambda *_args, **_kwargs: None)

    cli._eval_command(
        SimpleNamespace(
            eval_command="label",
            trace_id="trace-1",
            group=None,
            status="approved",
            answer="42",
            value=[],
            route="rlm",
            critical=False,
            reviewer="reviewer-1",
        ),
        settings,
    )

    assert calls["open"][1]["schema"] == "fleet_rlm_lite"
    assert calls["label_args"] == ("session-1", "turn-1", "a" * 64)
    assert calls["labels"]["expected_answer"] == "42"
    assert "context_files" not in calls["labels"]
    assert calls["closed"] is True


def test_trace_verify_command_routes_scope_to_the_trace_handler(monkeypatch) -> None:
    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "_trace_verify_command", lambda args, _settings: captured.append(args))

    cli.main(["trace", "verify", "trace:/catalog.schema/id", "--scope", "optimization"])

    assert captured[0].trace_id == "trace:/catalog.schema/id"
    assert captured[0].scope == "optimization"


def _local_trace_settings():
    from fleet_rlm.config import MlflowTarget

    return SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
    )


def test_trace_verify_flushes_and_requires_a_readable_trace(monkeypatch) -> None:
    import mlflow

    settings = _local_trace_settings()
    flushed = []
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(trace_location=None))
    monkeypatch.setattr(mlflow, "get_trace", lambda trace_id, **kwargs: flushed.append((trace_id, kwargs)) or None)

    import pytest

    with pytest.raises(SystemExit, match="was not found after flushing"):
        cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="tr-missing"), settings)

    assert flushed == [("tr-missing", {"silent": True, "flush": True})]


def test_trace_verify_confirms_stored_span_count(monkeypatch, capsys) -> None:
    import mlflow

    settings = _local_trace_settings()
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(trace_location=None))
    monkeypatch.setattr(
        mlflow,
        "get_trace",
        lambda _trace_id, **_kwargs: SimpleNamespace(data=SimpleNamespace(spans=[object(), object()])),
    )

    cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="tr-found"), settings)

    assert "Trace export verified: tr-found (2 spans; file:///tmp/mlruns)" in capsys.readouterr().out


def _databricks_trace_settings():
    from fleet_rlm.config import MlflowTarget

    return SimpleNamespace(
        mlflow_target=lambda: MlflowTarget(
            "databricks", "databricks", "/Shared/runtime", "/Shared/eval", "/Shared/opt"
        ),
        mlflow_trace_catalog="catalog",
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
        mlflow_tracing_sql_warehouse_id="warehouse-id",
    )


def test_trace_verify_requires_the_configured_unity_catalog_destination(monkeypatch) -> None:
    import mlflow
    import pytest
    from mlflow.entities.trace_location import UnityCatalog

    settings = _databricks_trace_settings()
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-value")
    experiment = SimpleNamespace(trace_location=UnityCatalog("catalog", "runtime_schema", "wrong_prefix"))
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    with pytest.raises(SystemExit, match="trace destination mismatch"):
        cli._trace_verify_command(
            SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings
        )


def test_trace_verify_checks_the_trace_itself_is_in_the_configured_destination(monkeypatch) -> None:
    import mlflow
    import pytest
    from mlflow.entities.trace_location import UnityCatalog

    settings = _databricks_trace_settings()
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-value")
    experiment = SimpleNamespace(trace_location=UnityCatalog("catalog", "runtime_schema", "runtime_prefix"))
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_location=UnityCatalog("other", "runtime_schema", "runtime_prefix")),
        data=SimpleNamespace(spans=[object()]),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(mlflow, "get_trace", lambda _trace_id, **_kwargs: trace)
    with pytest.raises(SystemExit, match="stored outside the configured destination"):
        cli._trace_verify_command(
            SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings
        )


def test_trace_verify_accepts_wrapped_unity_catalog_location(monkeypatch, capsys) -> None:
    import mlflow
    from mlflow.entities.trace_location import TraceLocation, TraceLocationType, UnityCatalog

    settings = _databricks_trace_settings()
    location = UnityCatalog("catalog", "runtime_schema", "runtime_prefix")
    experiment = SimpleNamespace(trace_location=location)
    trace = SimpleNamespace(
        info=SimpleNamespace(
            trace_location=TraceLocation(type=TraceLocationType.UC_TABLE_PREFIX, uc_table_prefix=location)
        ),
        data=SimpleNamespace(spans=[object()]),
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(mlflow, "get_trace", lambda _trace_id, **_kwargs: trace)

    cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="trace:/catalog.runtime_schema/id"), settings)

    output = " ".join(capsys.readouterr().out.split())
    assert (
        "Trace export verified: trace:/catalog.runtime_schema/id (1 spans; catalog.runtime_schema.runtime_prefix)"
    ) in output


def test_web_cli_serves_textual_client_after_verifying_api_destination(monkeypatch) -> None:
    from contextlib import nullcontext

    settings = FleetSettings(_env_file=None)
    calls = {}
    plan = cli.launcher.LaunchPlan(api_url="http://fleet.test", start_api=False)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.launcher,
        "plan_launch",
        lambda **kwargs: calls.update(plan=kwargs) or plan,
    )
    monkeypatch.setattr(cli.launcher, "running_services", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        cli.launcher,
        "verify_api_destination",
        lambda *args: calls.update(verified=args),
    )
    monkeypatch.setattr(cli, "serve_web_client", lambda **kwargs: calls.update(web=kwargs))

    cli.main(
        [
            "web",
            "--url",
            "http://fleet.test",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--session",
            "browser-session",
        ]
    )

    assert calls["plan"]["url"] == "http://fleet.test"
    assert calls["verified"][0] == "http://fleet.test"
    assert calls["web"]["host"] == "127.0.0.1"
    assert calls["web"]["port"] == 8765
    assert calls["web"]["session_id"] == "browser-session"
    assert "FLEET_SETUP_TOKEN" not in plan.api_env


def test_web_owned_api_uses_private_operator_capability_only_for_setup(monkeypatch) -> None:
    from contextlib import nullcontext

    settings = FleetSettings(_env_file=None)
    calls = {}
    plan = cli.launcher.LaunchPlan(
        api_url="http://127.0.0.1:8000",
        start_api=True,
        start_mlflow=True,
        mlflow_url="http://127.0.0.1:5001",
        api_env={"FLEET_MLFLOW_LOCAL_PORT": "5001"},
    )
    monkeypatch.delenv("FLEET_SETUP_TOKEN", raising=False)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "plan_launch", lambda **_kwargs: plan)

    def running_services(selected_plan, **kwargs):
        calls["serving_plan"] = selected_plan
        calls["services"] = kwargs
        return nullcontext(selected_plan)

    monkeypatch.setattr(cli.launcher, "running_services", running_services)
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)
    monkeypatch.setattr(cli, "serve_web_client", lambda **kwargs: calls.update(web=kwargs))

    cli.main(["web", "--host", "127.0.0.1", "--port", "8765"])

    token = plan.api_env["FLEET_SETUP_TOKEN"]
    assert token and calls["serving_plan"].api_env["FLEET_SETUP_TOKEN"] == token
    assert plan.api_env["FLEET_SETUP_PROJECT_DIR"]
    assert "FLEET_SETUP_DERIVED_ENV" in plan.api_env
    assert "setup_token" not in calls["web"]


def test_managed_optimize_reads_private_settings_before_lazy_optimizer_import(monkeypatch, tmp_path) -> None:
    from fleet_rlm.optimization_runs import worker_settings_payload

    settings = FleetSettings(
        _env_file=None,
        compiled_program_dir=str(tmp_path),
        mlflow_mode="local",
    )
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: pytest.fail("managed worker must use stdin settings"))
    original_reader = cli.read_worker_settings
    events: list[str] = []

    def read_settings(stream):
        events.append("read settings")
        return original_reader(stream)

    monkeypatch.setattr(cli, "read_worker_settings", read_settings)
    monkeypatch.setattr(sys, "stdin", io.StringIO(worker_settings_payload(settings).decode()))
    fake_gepa = types.SimpleNamespace(run=lambda args, parser, selected: events.append(("run", args, parser, selected)))
    fake_optimizer_package = types.ModuleType("fleet_rlm.optimize")
    fake_optimizer_package.gepa = fake_gepa
    monkeypatch.setitem(sys.modules, "fleet_rlm.optimize", fake_optimizer_package)
    real_import = builtins.__import__

    def tracked_import(name, *args, **kwargs):
        fromlist = kwargs.get("fromlist", args[2] if len(args) > 2 else ())
        if name == "fleet_rlm.optimize" and "gepa" in fromlist:
            events.append("import optimizer")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracked_import)

    cli.main(
        [
            "optimize",
            "--session",
            "sess_worker",
            "--managed-run-id",
            "sess_worker-run",
            "--managed-owner-id",
            "api-owner",
        ]
    )

    assert events[:2] == ["read settings", "import optimizer"]
    run = events[2]
    assert run[0] == "run"
    assert run[1].managed_run_id == "sess_worker-run"
    assert run[1].managed_owner_id == "api-owner"
    assert run[2].prog == "fleet optimize"
    assert run[3].compiled_program_dir == str(tmp_path)


@pytest.mark.parametrize(
    "allow_public,arguments,message",
    [
        (False, ["--public-url", "https://fleet.example.test"], None),
        (True, [], "public-url is required"),
    ],
)
def test_web_cli_rejects_invalid_public_listener(monkeypatch, capsys, allow_public, arguments, message):
    settings = FleetSettings(_env_file=None, allow_non_loopback_bind=allow_public)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(
        cli.launcher,
        "plan_launch",
        lambda **_kwargs: pytest.fail("invalid public listener must fail before planning services"),
    )

    with pytest.raises(SystemExit) as error:
        cli.main(["web", "--url", "http://fleet.test", "--host", "0.0.0.0", *arguments])

    assert error.value.code == 2
    if message:
        assert message in capsys.readouterr().err
