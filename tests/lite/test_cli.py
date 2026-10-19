from __future__ import annotations

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
            SimpleNamespace(eval_command="run", seed=7, oolong=0, limit=1, url="http://test", collect_only=True),
            settings,
        )
    assert captured["experiment_id"] == "eval-id"
    assert captured["uc_location"] is None
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


def test_trace_verify_flushes_and_requires_a_readable_trace(monkeypatch) -> None:
    import mlflow

    from fleet_rlm.config import MlflowTarget

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
    )
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

    from fleet_rlm.config import MlflowTarget

    settings = SimpleNamespace(
        mlflow_target=lambda: MlflowTarget("local", "file:///tmp/mlruns", "runtime", "eval", "opt"),
        mlflow_trace_schema="runtime_schema",
        mlflow_trace_table_prefix="runtime_prefix",
        mlflow_eval_trace_table_prefix="eval_prefix",
        mlflow_optimization_trace_schema="optimization_schema",
        mlflow_optimization_trace_table_prefix="optimization_prefix",
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda _name: SimpleNamespace(trace_location=None))
    monkeypatch.setattr(
        mlflow,
        "get_trace",
        lambda _trace_id, **_kwargs: SimpleNamespace(data=SimpleNamespace(spans=[object(), object()])),
    )

    cli._trace_verify_command(SimpleNamespace(scope="runtime", trace_id="tr-found"), settings)

    assert "Trace export verified: tr-found (2 spans; file:///tmp/mlruns)" in capsys.readouterr().out


def test_trace_verify_requires_the_configured_unity_catalog_destination(monkeypatch) -> None:
    import mlflow
    import pytest
    from mlflow.entities.trace_location import UnityCatalog

    from fleet_rlm.config import MlflowTarget

    settings = SimpleNamespace(
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

    from fleet_rlm.config import MlflowTarget

    settings = SimpleNamespace(
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

    from fleet_rlm.config import MlflowTarget

    settings = SimpleNamespace(
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
