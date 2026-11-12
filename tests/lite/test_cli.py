"""CLI commands, launcher ownership, and MLflow backend selection."""

from __future__ import annotations

import builtins
import io
import json
import os
import signal
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fleet_rlm import cli, launcher
from fleet_rlm.api import app as api
from fleet_rlm.config import FleetSettings
from fleet_rlm.launcher import LaunchError, LaunchPlan, ManagedProcess, plan_launch
from fleet_rlm.tui import commands

# CLI entry points


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
        build_judge_lm=lambda: None,
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: None),
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
    monkeypatch.setattr("fleet_rlm.optimize.evaluation.selected_snapshot", lambda *_args: {"task": "immutable source"})
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
    monkeypatch.setattr(cli.get_settings, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr(cli.launcher, "plan_launch", lambda **_kwargs: plan)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda *_args: False)

    def complete_setup(_args, _backend, token):
        calls["setup_token"] = token
        return True

    monkeypatch.setattr(cli, "_onboard", complete_setup)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)

    def running_services(selected_plan, **kwargs):
        calls["serving_plan"] = selected_plan
        calls["services"] = kwargs
        return nullcontext(selected_plan)

    monkeypatch.setattr(cli.launcher, "running_services", running_services)
    monkeypatch.setattr(cli.launcher, "verify_api_destination", lambda *_args: None)
    monkeypatch.setattr(cli, "serve_web_client", lambda **kwargs: calls.update(web=kwargs))

    cli.main(["web", "--host", "127.0.0.1", "--port", "8765"])

    token = plan.api_env["FLEET_SETUP_TOKEN"]
    assert token and calls["setup_token"] == token
    assert calls["serving_plan"].api_env["FLEET_SETUP_TOKEN"] == token
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
            "--managed-run-id",
            "optimize-worker-run",
            "--managed-owner-id",
            "api-owner",
        ]
    )

    assert events[:2] == ["read settings", "import optimizer"]
    run = events[2]
    assert run[0] == "run"
    assert run[1].managed_run_id == "optimize-worker-run"
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


def test_web_cli_requires_authentication_before_starting_services(monkeypatch, capsys):
    settings = FleetSettings(_env_file=None, allow_non_loopback_bind=True)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.delenv("FLEET_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        cli.launcher, "plan_launch", lambda **_kwargs: pytest.fail("must reject before starting services")
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["web", "--url", "http://fleet.test", "--host", "0.0.0.0", "--public-url", "https://fleet.test"])
    assert error.value.code == 2
    assert "FLEET_WEB_AUTH_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["cli", "web"])
def test_inherited_databricks_port_is_rejected_before_onboarding(monkeypatch, capsys, command):
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks")
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "_onboard", lambda *_args: pytest.fail("invalid port must not apply onboarding"))
    with pytest.raises(SystemExit) as error:
        cli.main([command, "--mlflow-port", "5001"])
    assert error.value.code == 2
    assert "--mlflow-port is only supported with local MLflow" in capsys.readouterr().err


def test_eval_score_cli_scores_collected_traces_without_running_turns(monkeypatch, capsys) -> None:
    import mlflow

    from fleet_rlm.config import MlflowTarget
    from fleet_rlm.optimize import evaluation

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        judge_environment=lambda: {},
        fleet_judge_model="databricks:/judge",
        build_jev_lm=lambda: "jev",
        build_judge_lm=lambda: "judge",
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: "adapter"),
    )
    traces = {"tr-1": object(), "tr-2": object()}
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda _name: SimpleNamespace(experiment_id="eval-id"))
    monkeypatch.setattr(mlflow, "get_trace", lambda trace_id, **_kw: traces.get(trace_id))
    captured = {}

    def score(received, **kwargs):
        captured.update(kwargs, traces=received)
        return SimpleNamespace(metrics={"quality/mean": 1.0})

    monkeypatch.setattr(evaluation, "score_evaluation_traces", score)
    monkeypatch.setattr(evaluation, "evaluate_cases", lambda *_a, **_k: pytest.fail("scoring must not rerun Turns"))
    cli._eval_command(SimpleNamespace(eval_command="score", trace_ids=["tr-1", "tr-2"]), settings)
    assert captured["traces"] == list(traces.values())
    assert captured["experiment_id"] == "eval-id" and captured["judge_lm"] == "judge" and captured["jev_lm"] == "jev"
    assert "quality/mean" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="tr-missing is not readable"):
        cli._eval_command(SimpleNamespace(eval_command="score", trace_ids=["tr-missing"]), settings)


def test_optimize_cli_takes_no_session_and_offers_check_and_a_token_cap(monkeypatch) -> None:
    from fleet_rlm.optimize import gepa

    captured = []
    settings = FleetSettings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda *_args: settings)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: True)
    monkeypatch.setattr(gepa, "run", lambda args, _parser, _settings: captured.append(args))
    with pytest.raises(SystemExit):
        cli.main(["optimize", "--session", "sess_a"])
    cli.main(["optimize", "--check", "--max-total-tokens", "0"])
    assert [(args.check, args.max_total_tokens) for args in captured] == [(True, 0)]


def test_score_diagnostics_only_skips_unrelated_judges(monkeypatch):
    import mlflow

    from fleet_rlm.optimize import evaluation, scorers

    calls = {}
    trace = object()
    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda settings: calls.update(environment=settings))
    monkeypatch.setattr(scorers, "make_execution_evidence_judge", lambda model: calls.update(model=model))
    monkeypatch.setattr(
        evaluation,
        "score_evaluation_traces",
        lambda traces, **kwargs: calls.update(traces=traces, options=kwargs) or SimpleNamespace(metrics={}),
    )
    settings = SimpleNamespace(fleet_judge_model="openai:/configured-judge")
    cli._score_command(SimpleNamespace(trace_ids=["saved"], execution_diagnostics_only=True), settings, "1")
    assert calls["environment"] is settings
    assert calls["traces"] == [trace]
    assert calls["model"] == settings.fleet_judge_model
    assert [s.name for s in calls["options"]["scorers"]] == ["execution_evidence_diagnostics"]
    assert "judge_lm" not in calls["options"]


def test_registered_score_uses_source_registry_and_evaluation_destination(monkeypatch):
    import mlflow

    from fleet_rlm.optimize import evaluation

    calls = {}
    trace, registered = object(), object()
    identities = [{"experiment_id": "1", "name": "diagnostics", "version": 1, "digest": "digest"}]
    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: trace)
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda _settings: None)
    monkeypatch.setattr(
        evaluation,
        "load_registered_scorers",
        lambda source, names: (calls.update(source=source, names=names) or [registered], identities),
    )
    monkeypatch.setattr(
        evaluation,
        "score_evaluation_traces",
        lambda traces, **kwargs: calls.update(traces=traces, options=kwargs) or SimpleNamespace(metrics={}),
    )
    cli._score_command(
        SimpleNamespace(trace_ids=["saved"], scorer_experiment_id="1", registered_scorer=["diagnostics"]),
        SimpleNamespace(fleet_judge_model="configured"),
        "49",
    )
    assert calls["source"] == "1"
    assert calls["options"]["experiment_id"] == "49"
    assert calls["options"]["scorers"] == [registered]
    assert calls["options"]["registered_provenance"] == identities
    assert calls["traces"] == [trace]


@pytest.mark.parametrize(
    "options",
    [
        {"scorer_experiment_id": "1"},
        {"registered_scorer": ["diagnostics"]},
        {"scorer_experiment_id": "1", "registered_scorer": ["diagnostics"], "execution_diagnostics_only": True},
    ],
)
def test_registered_score_rejects_incomplete_or_conflicting_selection(monkeypatch, options):
    import mlflow

    from fleet_rlm.optimize import evaluation

    monkeypatch.setattr(mlflow, "get_trace", lambda *_a, **_k: object())
    monkeypatch.setattr(evaluation, "apply_judge_environment", lambda _settings: None)
    monkeypatch.setattr(evaluation, "score_evaluation_traces", lambda *_a, **_k: pytest.fail("must not score"))
    with pytest.raises(SystemExit):
        cli._score_command(SimpleNamespace(trace_ids=["saved"], **options), object(), "49")


# Local service ownership


def _plan(**overrides: Any) -> LaunchPlan:
    options: dict[str, Any] = {
        "url": None,
        "host": "127.0.0.1",
        "port": 8000,
        "settings": FleetSettings(_env_file=None),
        "api_healthy": lambda _url: False,
        "mlflow_healthy": lambda _url: False,
    }
    return plan_launch(**{**options, **overrides})


def test_explicit_url_starts_nothing(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    plan = _plan(url="http://example.test:9000/")
    assert (plan.api_url, plan.start_api, plan.start_mlflow) == ("http://example.test:9000", False, False)


def test_a_healthy_api_is_reused_and_nothing_is_started(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)
    plan = _plan(api_healthy=lambda _url: True)
    assert not plan.start_api and not plan.start_mlflow and plan.api_env == {}
    assert any("Reusing the API server" in note for note in plan.notes)


def test_default_starts_local_mlflow_then_the_api_pointed_at_it() -> None:
    plan = _plan()
    assert plan.start_mlflow and plan.start_api and plan.mlflow_url == "http://127.0.0.1:5001"
    assert plan.api_env["FLEET_MLFLOW_LOCAL_PORT"] == "5001"
    assert plan.api_env["FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME"] == "fleet-rlm-dev"


def test_a_healthy_mlflow_is_reused_but_the_api_still_points_at_it() -> None:
    plan = _plan(mlflow_healthy=lambda _url: True, settings=FleetSettings(_env_file=None, mlflow_local_port=5002))
    assert plan.start_api and not plan.start_mlflow
    assert plan.api_env["FLEET_MLFLOW_LOCAL_PORT"] == "5002"
    assert any("Reusing the MLflow server" in note for note in plan.notes)


def test_databricks_mode_leaves_the_configured_tracking_alone() -> None:
    plan = _plan(settings=FleetSettings(_env_file=None, mlflow_mode="databricks"))
    assert (
        plan.start_api
        and not plan.start_mlflow
        and plan.api_env["FLEET_MLFLOW_DATABRICKS_TRACKING_URI"] == "databricks"
        and plan.mlflow_url is None
    )


def test_wait_ready_reports_a_process_that_exits_with_its_log(tmp_path: Path) -> None:
    process = ManagedProcess(
        "Demo", [sys.executable, "-c", "print('boom: bad config'); raise SystemExit(3)"], tmp_path / "demo.log"
    )
    process.start()
    with pytest.raises(LaunchError, match=r"exited during startup \(code 3\)[\s\S]*boom: bad config"):
        process.wait_ready(lambda: False, timeout=10, interval=0.05)


def test_wait_ready_times_out_and_stop_terminates_the_process(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    with pytest.raises(LaunchError, match="not ready after"):
        process.wait_ready(lambda: False, timeout=0.3, interval=0.05)
    assert process._process is not None and process._process.poll() is None
    process.stop(grace=5)
    assert process._process is not None and process._process.poll() is not None


def test_wait_ready_returns_once_the_check_passes(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    try:
        calls = iter([False, False, True])
        assert process.wait_ready(lambda: next(calls), timeout=10, interval=0.01) >= 0
    finally:
        process.stop(grace=5)


def test_stop_kills_the_whole_process_group_including_grandchildren(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    process = ManagedProcess("Demo", [sys.executable, "-c", script], tmp_path / "demo.log")
    process.start()
    deadline = time.monotonic() + 10
    while not process.tail().strip().isdigit() and time.monotonic() < deadline:
        time.sleep(0.05)
    grandchild = int(process.tail().strip())
    process.stop(grace=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("grandchild process survived stop()")


class _FakeProcess:
    def __init__(self, name: str, events: list[str], log_path: Path) -> None:
        self.name, self.events, self.log_path = name, events, log_path

    def start(self) -> None:
        self.events.append(f"start {self.name}")

    def wait_ready(self, _check: Any, _timeout: float) -> float:
        self.events.append(f"ready {self.name}")
        return 0.0

    def stop(self, grace: float | None = 10.0) -> None:
        self.events.append(f"stop {self.name}")
        self.events.append(f"grace {self.name} {grace}")


def _patch_fake_processes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(launcher, "_mlflow_process", lambda _port, _data: _FakeProcess("mlflow", events, Path("m.log")))
    monkeypatch.setattr(
        launcher, "_api_process", lambda _plan, _host, _port: _FakeProcess("api", events, Path("a.log"))
    )
    return events


def _run_services(plan: LaunchPlan, body: Any = None) -> list[str]:
    messages: list[str] = []
    with launcher.running_services(
        plan, host="127.0.0.1", port=8000, mlflow_port=5001, startup_timeout=5, log=messages.append
    ):
        if body:
            body()
    return messages


def test_services_start_mlflow_first_and_stop_the_api_first(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    _run_services(_plan())
    assert events == [
        "start mlflow",
        "ready mlflow",
        "start api",
        "ready api",
        "stop api",
        "grace api None",
        "stop mlflow",
        "grace mlflow 10.0",
    ]


def test_services_are_stopped_when_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    def boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_services(_plan(), boom)
    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_shutdown_ignores_repeated_signals_and_restores_handlers(monkeypatch, interrupted) -> None:
    events = _patch_fake_processes(monkeypatch)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    original_stop = _FakeProcess.stop

    def stop(process, grace: float | None = 10.0) -> None:
        # Deliver real signals while the launcher is waiting for owned services.
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGTERM)
        original_stop(process, grace)

    monkeypatch.setattr(_FakeProcess, "stop", stop)

    def body() -> None:
        if interrupted:
            raise KeyboardInterrupt

    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            _run_services(_plan(), body)
    else:
        _run_services(_plan(), body)

    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_shutdown_restores_handlers_and_unregisters_cleanup_on_failure(monkeypatch) -> None:
    _patch_fake_processes(monkeypatch)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    unregistered = []
    monkeypatch.setattr(launcher.atexit, "register", lambda _callback: None)
    monkeypatch.setattr(launcher.atexit, "unregister", unregistered.append)

    def stop(_process, grace: float | None = 10.0) -> None:
        _ = grace
        raise RuntimeError("stop failed")

    monkeypatch.setattr(_FakeProcess, "stop", stop)
    with pytest.raises(RuntimeError, match="stop failed"):
        _run_services(_plan())

    assert len(unregistered) == 1
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers


def test_a_failed_api_start_still_stops_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    class FailingApi(_FakeProcess):
        def wait_ready(self, _check: Any, _timeout: float) -> float:
            raise LaunchError("API server exited during startup")

    monkeypatch.setattr(launcher, "_api_process", lambda _plan, _host, _port: FailingApi("api", events, Path("a.log")))
    with pytest.raises(LaunchError):
        _run_services(_plan())
    assert events[-4:] == ["stop api", "grace api None", "stop mlflow", "grace mlflow 10.0"]


def _stub_cli(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_onboard", lambda *_args: True)
    monkeypatch.setattr(launcher, "verify_api_destination", lambda *_args: None)

    class Services:
        def __init__(self, plan: LaunchPlan, **kwargs: Any) -> None:
            seen["plan"], seen["kwargs"] = plan, kwargs

        def __enter__(self) -> LaunchPlan:
            return seen["plan"]

        def __exit__(self, *_exc: Any) -> None:
            seen["exited"] = True

    monkeypatch.setattr(cli.launcher, "running_services", Services)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **kw: seen.setdefault("ran", kw))
    return seen


def test_fleet_cli_starts_services_then_runs_the_client_against_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1"])
    plan = seen["plan"]
    assert plan.start_api and plan.start_mlflow and seen["exited"]
    assert seen["ran"]["base_url"] == plan.api_url
    assert seen["ran"]["session_id"] == "s1"
    assert seen["ran"]["settings"].mlflow_mode == "local"
    assert seen["ran"]["setup_token"] == plan.api_env["FLEET_SETUP_TOKEN"]
    assert seen["ran"]["setup_token"]
    assert plan.api_env["FLEET_SETUP_PROJECT_DIR"]
    assert "FLEET_SETUP_DERIVED_ENV" in plan.api_env


def test_fleet_cli_with_an_explicit_url_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1", "--url", "http://remote.test:9000"])
    assert not seen["plan"].start_api and seen["ran"]["base_url"] == "http://remote.test:9000"
    assert seen["ran"]["setup_token"] == ""
    assert "FLEET_SETUP_TOKEN" not in seen["plan"].api_env


@pytest.mark.parametrize("during_startup", [False, True])
def test_fleet_cli_handles_interrupt_without_traceback(monkeypatch, capsys, during_startup) -> None:
    seen = _stub_cli(monkeypatch)

    def interrupt(**_kwargs) -> None:
        raise KeyboardInterrupt

    if during_startup:
        monkeypatch.setattr(cli.launcher, "plan_launch", interrupt)
    else:
        monkeypatch.setattr(cli, "run_interactive_cli", interrupt)

    with pytest.raises(SystemExit) as error:
        cli.main(["cli"])

    assert error.value.code == 130
    assert during_startup or seen["exited"]
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err


def test_fleet_cli_reports_a_startup_failure_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> None:
            raise LaunchError("API server exited during startup (code 1).")

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(cli, "_onboard", lambda *_args: True)
    monkeypatch.setattr(cli.launcher, "running_services", Failing)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **_kw: pytest.fail("client must not start"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["cli"])
    assert exit_info.value.code == 1


def test_setup_restart_preserves_session_and_explicit_startup_timeout(monkeypatch):
    _stub_cli(monkeypatch)
    services = launcher.running_services
    timeouts, sessions = [], []
    results = iter(["setup", None])

    def start(plan, **kwargs):
        timeouts.append(kwargs["startup_timeout"])
        return services(plan, **kwargs)

    def run(**kwargs):
        sessions.append(kwargs["session_id"])
        return next(results)

    monkeypatch.setattr(launcher, "running_services", start)
    monkeypatch.setattr(cli, "run_interactive_cli", run)
    monkeypatch.setattr("fleet_rlm.tui.setup.run_setup", lambda *_args, **_kw: True)
    cli.main(["cli", "--session", "same-session", "--startup-timeout", "360"])
    assert sessions == ["same-session", "same-session"]
    assert timeouts == [360.0, 360.0]


def _fake_drain_client(monkeypatch: pytest.MonkeyPatch, events: list[str], states: list[dict[str, Any]]):
    class FakeClient:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: Any) -> None:
            self.base_url = base_url
            events.append(f"client {base_url}")
            events.append(f"auth {headers.get('Authorization')}")
            events.append(f"timeout {timeout.connect}")

        def __enter__(self):
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def post(self, path: str):
            events.append(f"POST {path}")
            return httpx.Response(
                200,
                json=states.pop(0),
                request=httpx.Request("POST", self.base_url + path),
            )

        def get(self, path: str):
            events.append(f"GET {path}")
            value = states.pop(0)
            if isinstance(value, Exception):
                raise value
            return httpx.Response(200, json=value, request=httpx.Request("GET", self.base_url + path))

    monkeypatch.setattr(launcher.httpx, "Client", FakeClient)


def test_drain_fences_admission_before_polling_and_waits_until_idle(monkeypatch) -> None:
    events: list[str] = []
    states = [
        {
            "accepting": False,
            "busy": True,
            "experiments": [{"run_id": "sess_run", "status": "running", "stage": "search"}],
        },
        {
            "accepting": False,
            "busy": True,
            "experiments": [{"run_id": "sess_run", "status": "running", "stage": "validation"}],
        },
        {"accepting": False, "busy": False, "experiments": []},
    ]
    _fake_drain_client(monkeypatch, events, states)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: events.append(f"sleep {seconds}"))
    plan = LaunchPlan(api_url="http://127.0.0.1:8000", api_env={"FLEET_SETUP_TOKEN": "operator-token"})
    messages: list[str] = []

    launcher._drain_experiments(plan, messages.append)

    assert events == [
        "client http://127.0.0.1:8000",
        "auth Bearer operator-token",
        "timeout 2.0",
        "POST /setup/experiments/drain",
        "sleep 1.0",
        "GET /setup/experiments/status",
        "sleep 1.0",
        "GET /setup/experiments/status",
    ]
    assert any("sess_run: search" in message for message in messages)
    assert any("sess_run: validation" in message for message in messages)
    assert not any("idle" in message.lower() or "done" in message.lower() for message in messages)


def test_owned_service_shutdown_drains_before_api_then_mlflow(monkeypatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    states = [
        {"accepting": False, "busy": True, "experiments": [{"run_id": "sess_wait", "status": "running"}]},
        {"accepting": False, "busy": False, "experiments": []},
    ]
    _fake_drain_client(monkeypatch, events, states)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: events.append(f"sleep {seconds}"))
    plan = LaunchPlan(
        api_url="http://127.0.0.1:8000",
        start_api=True,
        start_mlflow=True,
        mlflow_url="http://127.0.0.1:5001",
        api_env={"FLEET_SETUP_TOKEN": "private-operator"},
    )

    _run_services(plan)

    drain_index = events.index("POST /setup/experiments/drain")
    api_stop_index = events.index("stop api")
    mlflow_stop_index = events.index("stop mlflow")
    assert drain_index < api_stop_index < mlflow_stop_index
    assert events[api_stop_index + 1] == "grace api None"
    assert events[mlflow_stop_index + 1] == "grace mlflow 10.0"


def test_reused_api_shutdown_does_not_drain_or_stop_remote_services(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("reused remote API must not be drained"),
    )
    events = _patch_fake_processes(monkeypatch)

    _run_services(LaunchPlan(api_url="http://remote.test:8000"))

    assert events == []


def test_drain_status_failure_logs_graceful_wait_without_claiming_idle(monkeypatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    request = httpx.Request("GET", "http://127.0.0.1:8000/setup/experiments/status")
    states = [
        {"accepting": False, "busy": True, "experiments": [{"run_id": "sess_unknown", "status": "running"}]},
        httpx.ConnectError("server unavailable", request=request),
    ]
    _fake_drain_client(monkeypatch, events, states)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: events.append(f"sleep {seconds}"))
    messages = _run_services(
        LaunchPlan(
            api_url="http://127.0.0.1:8000",
            start_api=True,
            start_mlflow=True,
            mlflow_url="http://127.0.0.1:5001",
            api_env={"FLEET_SETUP_TOKEN": "private-operator"},
        )
    )

    assert any(
        "Could not observe experiment drain; waiting for API graceful shutdown." in message for message in messages
    )
    assert events.index("POST /setup/experiments/drain") < events.index("stop api") < events.index("stop mlflow")
    assert events[events.index("stop api") + 1] == "grace api None"
    assert events[events.index("stop mlflow") + 1] == "grace mlflow 10.0"


def test_managed_process_stop_accepts_unbounded_grace_without_force_kill(monkeypatch, tmp_path: Path) -> None:
    class Process:
        pid = 123
        returncode = None

        def __init__(self) -> None:
            self.wait_timeout: float | None = -1

        def poll(self):
            return self.returncode

        def wait(self, timeout: float | None = None):
            self.wait_timeout = timeout
            self.returncode = 0
            return 0

        def send_signal(self, _signal: int) -> None:
            return None

    process = ManagedProcess("API", [], tmp_path / "api.log")
    child = Process()
    process._process = child  # type: ignore[assignment]
    signals: list[int] = []
    monkeypatch.setattr(ManagedProcess, "_signal", staticmethod(lambda _process, sig: signals.append(sig)))

    process.stop(grace=None)

    assert signals == [signal.SIGTERM]
    assert child.wait_timeout is None
    assert child.returncode == 0


# MLflow backend selection


def test_profiles_have_independent_precedence_and_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fleet.toml").write_text(
        '[config]\nschema_version=5\n[mlflow.local]\nexperiment_name="local-toml"\n'
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


def test_configured_databricks_destinations_keep_the_three_experiments_separate():
    settings = FleetSettings(_env_file=None, mlflow_mode="databricks")
    target = settings.mlflow_target()

    assert target.tracking_uri == "databricks"
    assert target.experiment_name == "/Shared/fleet-rlm-runtime"
    assert target.eval_experiment_name == "/Shared/fleet-rlm-eval"
    assert target.optimization_experiment_name == "/Shared/fleet-rlm-gepa"
    assert (settings.mlflow_trace_catalog, settings.mlflow_trace_schema, settings.mlflow_trace_table_prefix) == (
        "example_catalog",
        "fleet_observability",
        "fleet_rlm_runtime",
    )
    assert (settings.mlflow_eval_trace_table_prefix, settings.mlflow_optimization_trace_schema) == (
        "fleet_rlm_eval",
        "fleet_gepa",
    )
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"


def test_databricks_trace_prefix_dotenv_overrides_toml_and_can_be_corrected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fleet.toml").write_text(
        '[config]\nschema_version=5\n[mlflow.databricks]\ntrace_table_prefix="fleet_rlm_dev"\n'
    )
    env = tmp_path / ".env"
    env.write_text("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_runtime\n")
    stale = FleetSettings(_env_file=env, mlflow_mode="databricks")
    assert stale.mlflow_trace_table_prefix == "fleet_rlm_runtime"
    assert stale.configuration_source("mlflow_databricks_trace_table_prefix") == "dotenv"

    env.write_text("FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_dev\n")
    corrected = FleetSettings(_env_file=env, mlflow_mode="databricks")
    assert corrected.mlflow_trace_table_prefix == "fleet_rlm_dev"
    assert corrected.configuration_source("mlflow_databricks_trace_table_prefix") == "dotenv"


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


def test_old_schema_requires_migration_and_toml_selects_mlflow_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    path.write_text("[config]\nschema_version=3\n")
    with pytest.raises(ValueError, match="schema_version = 5"):
        FleetSettings(_env_file=None)
    path.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    assert FleetSettings(_env_file=None).mlflow_mode == "databricks"


@pytest.mark.parametrize("arguments", [["databricks"], ["--mlflow", "databricks"], ["--session", "s1", "databricks"]])
def test_cli_selects_databricks_and_propagates_the_profile(monkeypatch, arguments):
    from contextlib import nullcontext

    seen = {}
    monkeypatch.setattr(cli, "_onboard", lambda *_args: True)
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.invalid")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "databricks")
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
    cli.main(["optimize", "--mlflow", backend])
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
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        databricks_host="https://workspace.invalid",
    )
    _respond(monkeypatch, _health(settings))
    monkeypatch.setattr(
        "mlflow.set_experiment", lambda *_args, **_kwargs: pytest.fail("must not create evaluation work")
    )
    with pytest.raises(SystemExit, match="destination mismatch"):
        cli._eval_command(SimpleNamespace(eval_command="run", url="http://fleet.test"), settings)


def test_databricks_health_rejects_another_workspace_before_evaluation(monkeypatch):
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        databricks_host="https://client.invalid",
    )
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


@pytest.mark.parametrize("failure", ["experiments", "registry", "store", "flush"])
def test_shutdown_attempts_every_owned_cleanup_after_a_failure(monkeypatch, tmp_path, failure):
    events = []

    def cleanup(name):
        events.append(name)
        if name == failure:
            raise RuntimeError(f"{name} cleanup failed")

    store = SimpleNamespace(initialize=lambda: None, close_migration_pool=lambda: None, close=lambda: cleanup("store"))

    class FakeExperimentManager:
        def __init__(self, _settings, _store):
            pass

        async def initialize(self):
            return None

        async def close(self):
            cleanup("experiments")

    monkeypatch.setattr(api.Store, "open", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(api, "ExperimentManager", FakeExperimentManager)
    monkeypatch.setattr(api, "_configure_tracing", lambda _settings: "17")
    monkeypatch.setattr(
        api, "SessionRuntimeRegistry", lambda *_args: SimpleNamespace(close=lambda: cleanup("registry"))
    )
    monkeypatch.setattr(api.mlflow, "flush_trace_async_logging", lambda: cleanup("flush"))
    app = api.create_app(settings=FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)))
    if failure == "flush":
        with TestClient(app):
            pass
    else:
        with pytest.raises(RuntimeError, match=f"{failure} cleanup failed"), TestClient(app):
            pass
    assert events == ["experiments", "registry", "store", "flush"]


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


def test_databricks_destination_mismatch_aborts_startup_with_safe_guidance(monkeypatch):
    from fleet_rlm import observability

    monkeypatch.setattr(observability, "capture_serving_snapshot", lambda: None)
    monkeypatch.setattr(api.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(api.mlflow.tracing, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(
        api.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(tags={"mlflow.experiment.databricksTraceDestinationPath": "private-marker"}),
    )
    monkeypatch.setattr(api.mlflow, "set_experiment", lambda *_args, **_kwargs: pytest.fail("must not activate"))
    app = api.create_app(settings=FleetSettings(_env_file=None, mlflow_mode="databricks"), initialize_db=False)

    with pytest.raises(RuntimeError, match="trace destination mismatch") as error, TestClient(app):
        pytest.fail("application must not become ready")

    message = str(error.value)
    assert "private-marker" not in message
    assert ".env overrides" in message
    for suffix in ("CATALOG", "SCHEMA", "TABLE_PREFIX"):
        assert f"FLEET_MLFLOW_DATABRICKS_TRACE_{suffix}" in message


def test_databricks_auth_uses_dotenv_credentials_but_named_profile_stays_explicit(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATABRICKS_HOST=https://workspace.invalid\nDATABRICKS_TOKEN=private-marker\n")
    settings = FleetSettings(_env_file=path, mlflow_mode="databricks", mlflow_databricks_tracking_uri="databricks")
    monkeypatch.setenv("DATABRICKS_HOST", "")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    settings.prepare_mlflow_auth()
    import os

    assert os.environ["DATABRICKS_TOKEN"] == "private-marker"
    assert os.environ["DATABRICKS_HOST"] == "https://workspace.invalid"
    monkeypatch.setenv("DATABRICKS_TOKEN", "existing")
    settings.with_explicit_overrides(mlflow_databricks_tracking_uri="databricks://named").prepare_mlflow_auth()
    assert os.environ["DATABRICKS_TOKEN"] == "existing"
    assert os.environ["DATABRICKS_CONFIG_PROFILE"] == "named"
    assert "private-marker" not in str(settings.configuration())


def test_databricks_named_profile_is_propagated_to_sdk_subprocesses():
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks://example-profile",
    )

    environment = settings.mlflow_environment()

    assert environment["FLEET_MLFLOW_DATABRICKS_TRACKING_URI"] == "databricks://example-profile"
    assert environment["DATABRICKS_CONFIG_PROFILE"] == "example-profile"
    assert "DATABRICKS_TOKEN" not in environment


def test_local_service_uses_configured_persistent_directory(tmp_path):
    data = tmp_path / "tracking"
    process = launcher._mlflow_process(5012, str(data))
    assert f"sqlite:///{data / 'mlflow.db'}" in process.command
    assert str(data / "artifacts") in process.command
    assert process.command[-2:] == ["--port", "5012"]


def test_optimize_inherits_backend_and_effective_profile_without_sdk_destination(monkeypatch, capsys):
    import time

    settings = FleetSettings(
        _env_file=None, mlflow_mode="databricks", mlflow_databricks_experiment_name="/Shared/custom"
    )
    state = commands.SessionState(mlflow_settings=settings)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={"job": {"run_id": "s1-run", "status": "running", "stage": "search"}},
            )
        return httpx.Response(200, json={"job": {"run_id": "s1-run", "status": "completed"}})

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "stale")
    with httpx.Client(base_url="http://fleet.test", transport=httpx.MockTransport(handle)) as client:
        ctx = commands.Context(client, "s1", state)
        commands._optimize(ctx, "--max-metric-calls 1")
        commands._optimize(ctx, "--mlflow=local")

    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url.path == "/improve/jobs"
    body = json.loads(requests[0].content)
    assert body["max_metric_calls"] == 1
    assert body["request_id"]
    assert "mlflow" not in body and "tracking_uri" not in body and "experiment_name" not in body
    assert "MLFLOW_EXPERIMENT_ID" not in requests[0].headers
    assert "MLFLOW_TRACKING_URI" not in requests[0].headers
    assert os.environ["MLFLOW_EXPERIMENT_ID"] == "stale"
    assert "session's MLflow backend" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["cli", "web"])
def test_invalid_configured_backend_still_opens_interactive_setup(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", "")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    seen = []
    monkeypatch.setattr(cli, "_onboard", lambda args, backend, _token: seen.append((args.command, backend)) or False)
    cli.main([command])
    assert seen == [(command, "local")]


def test_toml_backend_is_used_by_standalone_commands_and_explicit_flag_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[mlflow]\nmode="databricks"\n')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setattr(launcher, "mlflow_is_healthy", lambda _url: True)
    seen = []
    monkeypatch.setattr(cli, "_trace_verify_command", lambda _args, settings: seen.append(settings.mlflow_mode))
    cli.main(["trace", "verify", "trace-1"])
    cli.main(["trace", "verify", "trace-1", "--mlflow", "local"])
    assert seen == ["databricks", "local"]
