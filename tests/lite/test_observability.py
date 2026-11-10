from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.parametrize("destination", [None, "", "catalog.schema.prefix"])
def test_databricks_trace_experiment_preserves_native_activation(monkeypatch, destination):
    from fleet_rlm import observability

    experiment = (
        None
        if destination is None
        else SimpleNamespace(tags={"mlflow.experiment.databricksTraceDestinationPath": destination})
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    calls = []
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda name, **kwargs: calls.append((name, kwargs)) or experiment
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix="prefix",
            tracing_sql_warehouse_id="warehouse",
        )
        is experiment
    )
    assert len(calls) == 1
    assert calls[0][0] == "/Shared/runtime"
    assert calls[0][1]["trace_location"].full_table_prefix == "catalog.schema.prefix"


@pytest.mark.parametrize("prefix", ["requested", ""])
def test_databricks_trace_destination_mismatch_prevents_activation(monkeypatch, prefix):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.existing"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda *_args, **_kwargs: pytest.fail("must not activate")
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    with pytest.raises(observability.MlflowTraceDestinationMismatchError, match="trace destination mismatch"):
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix=prefix,
        )


def test_databricks_trace_experiment_omitted_prefix_uses_native_experiment_id(monkeypatch):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.17"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(observability.mlflow, "set_experiment", lambda *_args, **_kwargs: experiment)
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime", databricks=True, trace_catalog="catalog", trace_schema="schema"
        )
        is experiment
    )


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.observability as memory_and_eval

    calls: dict[str, Any] = {}
    monkeypatch.setattr(memory_and_eval.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("uri", uri))
    monkeypatch.setattr(
        memory_and_eval.mlflow.tracing,
        "configure",
        lambda **kwargs: calls.setdefault("processors", kwargs),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "set_experiment",
        lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "dspy",
        SimpleNamespace(autolog=lambda **kwargs: calls.setdefault("autolog", kwargs)),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(experiment_id="experiment-id"),
    )

    experiment_id = memory_and_eval.configure_mlflow_observability(
        "fleet-rlm-test",
        "sqlite:///mlflow-test.db",
    )

    assert experiment_id == "experiment-id"
    assert calls == {
        "uri": "sqlite:///mlflow-test.db",
        "experiment": "fleet-rlm-test",
        "processors": {"span_processors": [memory_and_eval.sanitize_trace_span]},
        "autolog": {
            "log_traces": True,
            "log_traces_from_compile": False,
            "log_traces_from_eval": False,
        },
    }


def test_trace_span_sanitization_redacts_secret_fields_and_bounds_content():
    import fleet_rlm.observability as memory_and_eval

    class Span:
        def __init__(self):
            self.inputs = {"token": "secret-value", "task": "Bearer abcdefghijklmnopqrstuvwxyz"}
            self.outputs = {"answer": "x" * (memory_and_eval._TRACE_STRING_LIMIT + 1)}

        def set_inputs(self, value):
            self.inputs = value

        def set_outputs(self, value):
            self.outputs = value

    span = Span()
    memory_and_eval.sanitize_trace_span(span)
    assert span.inputs["token"] == "[redacted]"
    assert "[redacted]" in span.inputs["task"]
    assert len(span.outputs["answer"]) < memory_and_eval._TRACE_STRING_LIMIT + 100


def test_native_required_skill_arguments_do_not_export_resource_bytes():
    from fleet_rlm.observability import trace_value
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: source\ndescription: Source\n---\nInstructions"
    skill = captured_required_skill("source", "workspace", (("SKILL.md", raw), ("secret.dat", b"private-resource")))
    exported = trace_value({"required_skills": (skill,)}, nodes=[0])
    assert exported == {"required_skills": ["[RequiredSkill omitted]"]}
    assert "private-resource" not in str(exported)


def test_trace_shows_reasoning_text_but_streams_still_redact_it() -> None:
    import fleet_rlm.observability as memory_and_eval

    cleaned = memory_and_eval.trace_value(
        {"reasoning_content": "Plan: use api_key=hidden", "api_key": "value"},
        nodes=[0],
    )
    assert cleaned == {"reasoning_content": "Plan: use api_key=[redacted]", "api_key": "[redacted]"}
    assert '"reasoning_content": "[redacted]"' in memory_and_eval.activity_preview({"reasoning_content": "private"})


def test_typesafe_route_filters_the_incompatible_mlflow_callback() -> None:
    from mlflow.dspy.callback import MlflowCallback

    from fleet_rlm.observability import without_mlflow_dspy_callback

    sse_callback = object()
    # Matching is by class identity, not by module/name strings, so a look-alike from elsewhere survives.
    impostor = type("MlflowCallback", (), {"__module__": "mlflow.dspy.callback"})()

    assert without_mlflow_dspy_callback([MlflowCallback(), sse_callback]) == [sse_callback]
    assert without_mlflow_dspy_callback([impostor, sse_callback]) == [impostor, sse_callback]


def test_snapshot_provenance_is_written_to_the_turn_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.engine.turn as turn_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, SNAPSHOT_NAME, clear_preflight_cache, preflight

    clear_preflight_cache()
    provider = SimpleNamespace(
        get=lambda name: SimpleNamespace(name=name, id="snap-1", state="active", cpu=4, mem=8, disk=8)
    )
    identity = preflight(provider, name=SNAPSHOT_NAME, api_key="key", org_id="org")
    expected = {
        "snapshot_name": SNAPSHOT_NAME,
        "snapshot_id": "snap-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": DEFAULT_DEFINITION.digest(),
    }
    captured: dict[str, Any] = {}

    class Span:
        def set_inputs(self, value: dict[str, Any]) -> None:
            captured["inputs"] = value

        def set_attributes(self, value: dict[str, Any]) -> None:
            captured["attributes"] = value

    monkeypatch.setattr(turn_module.mlflow, "update_current_trace", lambda **_kwargs: None)
    runtime = SimpleNamespace(
        configuration_overrides={},
        engine=SimpleNamespace(execution_mode="hybrid", root_lm=None, sub_lm=None),
        sandbox_mgr=SimpleNamespace(
            turn_timeout_seconds=900,
            snapshot_provenance=identity.provenance,
            deadline_provenance=lambda: {"deadline_source": "configuration", "effective_seconds": 900},
        ),
    )

    turn_module._describe_turn(
        Span(),
        runtime,
        FleetSettings(_env_file=None),
        "session-a",
        "compute a digit",
        "turn-1",
        None,
        {"manifest": "/workspace/files (empty)", "context_files": {}},
        None,
    )

    attributes = captured["attributes"]
    assert {key: attributes[key] for key in expected} == expected
    assert attributes["deadline_source"] == "configuration"
    assert attributes["effective_seconds"] == 900
    assert "definition_digest" not in attributes
