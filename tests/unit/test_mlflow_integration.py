from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.analytics.config import MlflowConfig
import fleet_rlm.analytics.mlflow_integration as mlflow_integration


@pytest.fixture(autouse=True)
def _reset_mlflow_integration_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mlflow_integration, "_INIT_IDENTITY", None)
    monkeypatch.setattr(mlflow_integration, "_INITIALIZED", False)
    monkeypatch.setattr(mlflow_integration, "_ACTIVE_CONFIG", None)
    yield


def test_initialize_mlflow_wires_tracking_experiment_autolog_and_callback(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}

    fake_mlflow = SimpleNamespace(
        set_tracking_uri=lambda uri: calls.__setitem__("tracking_uri", uri),
        set_experiment=lambda **kwargs: calls.__setitem__("experiment", kwargs),
        dspy=SimpleNamespace(
            autolog=lambda **kwargs: calls.__setitem__("autolog", kwargs)
        ),
    )

    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: fake_mlflow)
    monkeypatch.setattr(mlflow_integration, "_existing_trace_callback", lambda: None)
    monkeypatch.setattr(
        mlflow_integration.dspy,
        "settings",
        SimpleNamespace(callbacks=[]),
    )
    monkeypatch.setattr(
        mlflow_integration.dspy,
        "configure",
        lambda **kwargs: calls.__setitem__("callbacks", kwargs["callbacks"]),
    )

    config = MlflowConfig(
        enabled=True,
        tracking_uri="http://127.0.0.1:6001",
        experiment="fleet-rlm-tests",
        dspy_log_traces_from_compile=True,
        dspy_log_traces_from_eval=False,
        dspy_log_compiles=True,
        dspy_log_evals=True,
    )

    assert mlflow_integration.initialize_mlflow(config) is True
    assert calls["tracking_uri"] == "http://127.0.0.1:6001"
    assert calls["experiment"] == {"experiment_name": "fleet-rlm-tests"}
    assert calls["autolog"] == {
        "log_traces": True,
        "log_traces_from_compile": True,
        "log_traces_from_eval": False,
        "log_compiles": True,
        "log_evals": True,
        "disable": False,
        "silent": True,
    }
    callbacks = calls["callbacks"]
    assert isinstance(callbacks, list)
    assert any(
        isinstance(callback, mlflow_integration.FleetMlflowTraceCallback)
        for callback in callbacks
    )


def test_initialize_mlflow_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    calls = {"tracking_uri": 0, "autolog": 0}
    fake_mlflow = SimpleNamespace(
        set_tracking_uri=lambda uri: calls.__setitem__(
            "tracking_uri", calls["tracking_uri"] + 1
        ),
        set_experiment=lambda **kwargs: None,
        dspy=SimpleNamespace(
            autolog=lambda **kwargs: calls.__setitem__("autolog", calls["autolog"] + 1)
        ),
    )

    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: fake_mlflow)
    monkeypatch.setattr(
        mlflow_integration, "_existing_trace_callback", lambda: object()
    )
    monkeypatch.setattr(
        mlflow_integration.dspy,
        "settings",
        SimpleNamespace(callbacks=[]),
    )

    config = MlflowConfig(enabled=True, tracking_uri="http://127.0.0.1:6001")

    assert initialize_mlflow(config) is True
    assert initialize_mlflow(config) is True
    assert calls["tracking_uri"] == 1
    assert calls["autolog"] == 1


def test_trace_result_metadata_returns_empty_when_mlflow_disabled():
    mlflow_integration._ACTIVE_CONFIG = MlflowConfig(enabled=False)
    with mlflow_integration.mlflow_request_context(
        mlflow_integration.MlflowTraceRequestContext(client_request_id="req-disabled")
    ):
        assert mlflow_integration.trace_result_metadata() == {}


def test_trace_result_metadata_includes_trace_and_client_request_id(
    monkeypatch: pytest.MonkeyPatch,
):
    mlflow_integration._ACTIVE_CONFIG = MlflowConfig(enabled=True)
    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: object())
    monkeypatch.setattr(
        mlflow_integration, "initialize_mlflow", lambda config=None: True
    )
    monkeypatch.setattr(
        mlflow_integration,
        "update_current_mlflow_trace",
        lambda response_preview=None: "trace-123",
    )

    with mlflow_request_context(MlflowTraceRequestContext(client_request_id="req-123")):
        assert trace_result_metadata() == {
            "mlflow_trace_id": "trace-123",
            "mlflow_client_request_id": "req-123",
        }


def test_sanitize_log_field_escapes_newlines_and_carriage_returns() -> None:
    assert (
        mlflow_integration._sanitize_log_field("trace-1\r\nforged-entry")
        == "trace-1\\r\\nforged-entry"
    )


def test_resolve_trace_by_client_request_id_uses_server_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    matching_trace = SimpleNamespace(
        info=SimpleNamespace(client_request_id="req-123", timestamp_ms=20)
    )
    older_trace = SimpleNamespace(
        info=SimpleNamespace(client_request_id="req-123", timestamp_ms=10)
    )
    fake_mlflow = SimpleNamespace(
        search_traces=lambda **kwargs: calls.append(kwargs)
        or [older_trace, matching_trace],
    )

    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: fake_mlflow)
    monkeypatch.setattr(
        mlflow_integration, "_trace_experiment_ids", lambda config=None: ["exp-1"]
    )

    resolved = mlflow_integration.resolve_trace_by_client_request_id("req-123")

    assert resolved is matching_trace
    assert calls == [
        {
            "experiment_ids": ["exp-1"],
            "filter_string": "trace.client_request_id = 'req-123'",
            "max_results": 5000,
            "return_type": "list",
            "include_spans": False,
        }
    ]


def test_update_current_mlflow_trace_skips_when_no_active_trace(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[dict[str, object]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        get_active_trace_id=lambda: None,
        update_current_trace=lambda **kwargs: calls.append(kwargs),
        get_last_active_trace_id=lambda thread_local=True: None,
    )

    mlflow_integration._ACTIVE_CONFIG = MlflowConfig(enabled=True)
    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: fake_mlflow)

    with mlflow_request_context(MlflowTraceRequestContext(client_request_id="req-123")):
        assert update_current_mlflow_trace(response_preview="done") is None

    assert calls == []


def test_flush_mlflow_traces_skips_exporters_without_async_queue(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Exporter:
        pass

    fake_mlflow = SimpleNamespace(
        flush_trace_async_logging=lambda terminate=False: pytest.fail(
            "flush_trace_async_logging should not be called without an async queue"
        )
    )

    mlflow_integration._ACTIVE_CONFIG = MlflowConfig(enabled=True)
    monkeypatch.setattr(mlflow_integration, "_import_mlflow", lambda: fake_mlflow)

    import mlflow.tracing.provider as provider

    monkeypatch.setattr(provider, "_get_trace_exporter", lambda: _Exporter())

    flush_mlflow_traces()


def test_trace_to_dataset_row_extracts_expectations_and_feedback():
    trace = SimpleNamespace(
        to_dict=lambda: {
            "info": {
                "trace_id": "trace-1",
                "client_request_id": "req-1",
                "request_preview": "fallback input",
                "response_preview": "fallback output",
                "trace_metadata": {
                    "mlflow.traceInputs": '{"question": "What is MLflow?"}',
                    "mlflow.traceOutputs": '"MLflow is an ML platform"',
                },
            }
        },
        search_assessments=lambda: [
            SimpleNamespace(
                to_dictionary=lambda: {
                    "assessment_name": "expected_response",
                    "expectation": {"value": "MLflow is an ML platform"},
                    "source": {"source_id": "user-a"},
                }
            ),
            SimpleNamespace(
                to_dictionary=lambda: {
                    "assessment_name": "response_is_correct",
                    "feedback": {"value": True},
                    "rationale": "Looks right",
                    "source": {"source_id": "user-a"},
                }
            ),
        ],
    )

    row = trace_to_dataset_row(trace)

    assert row["trace_id"] == "trace-1"
    assert row["client_request_id"] == "req-1"
    assert row["inputs"] == {"question": "What is MLflow?"}
    assert row["outputs"] == "MLflow is an ML platform"
    assert row["expectations"] == {"expected_response": "MLflow is an ML platform"}
    assert row["feedback"]["response_is_correct"]["value"] is True
