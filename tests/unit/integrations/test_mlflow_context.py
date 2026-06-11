from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def test_mlflow_request_context_initializes_mlflow_at_turn_entry(monkeypatch) -> None:
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    init_calls: list[bool] = []

    fake_mlflow = SimpleNamespace(
        start_span=lambda **kwargs: _FakeSpanContext(),
        get_current_active_span=object,
        get_active_trace_id=lambda: None,
        update_current_trace=lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_runtime._import_mlflow",
        lambda: fake_mlflow,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_runtime.get_mlflow_config",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
        lambda _config: init_calls.append(True) or True,
    )
    monkeypatch.setenv("MLFLOW_ENABLED", "true")

    with mlflow_request_context(MlflowTraceRequestContext(client_request_id="chat-init")):
        pass

    assert init_calls == [True]


class _FakeSpanContext:
    def __enter__(self):
        return SimpleNamespace(set_inputs=lambda *args, **kwargs: None, set_outputs=lambda *args, **kwargs: None)

    def __exit__(self, exc_type, exc, tb):
        return False


def test_update_current_mlflow_trace_mirrors_fleet_metadata_to_tags(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured: list[dict[str, Any]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=object,
        get_active_trace_id=lambda: "tr-test",
        update_current_trace=lambda **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: SimpleNamespace(active_model_id=None),
            flush_mlflow_traces=lambda: None,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    with mlflow_request_context(
        MlflowTraceRequestContext(
            client_request_id="chat-123",
            session_id="workspace:user:session",
            user_id="user",
            app_env="local",
            request_preview="hello",
            metadata={"fleet_rlm.routing_decision": "url_document_rlm"},
        )
    ):
        mlflow_context.update_current_mlflow_trace(trace_metadata={"fleet_rlm.source_url": "https://dspy.ai"})

    update = captured[0]
    assert update["tags"]["fleet_rlm.trace_kind"] == "application"
    assert update["tags"]["fleet_rlm.client_request_id"] == "chat-123"
    assert update["tags"]["fleet_rlm.session_id"] == "workspace:user:session"
    assert update["tags"]["fleet_rlm.routing_decision"] == "url_document_rlm"
    assert update["tags"]["fleet_rlm.source_url"] == "https://dspy.ai"


def test_update_current_mlflow_trace_does_not_resend_active_trace_tags(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured: list[dict[str, Any]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=object,
        get_active_trace_id=lambda: "tr-test",
        update_current_trace=lambda **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: SimpleNamespace(active_model_id=None),
            flush_mlflow_traces=lambda: None,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    with mlflow_request_context(
        MlflowTraceRequestContext(
            client_request_id="chat-123",
            session_id="workspace:user:session",
            user_id="user",
            metadata={"fleet_rlm.routing_decision": "url_document_rlm"},
        )
    ):
        mlflow_context.update_current_mlflow_trace(trace_metadata={"fleet_rlm.source_url": "https://dspy.ai"})
        mlflow_context.update_current_mlflow_trace(trace_metadata={"fleet_rlm.source_url": "https://dspy.ai"})

    assert captured[0]["tags"]["fleet_rlm.routing_decision"] == "url_document_rlm"
    assert captured[0]["tags"]["fleet_rlm.source_url"] == "https://dspy.ai"
    assert captured[1]["tags"] is None


def test_update_current_mlflow_trace_sets_tags_on_resolved_inactive_trace(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured_tags: list[tuple[str, str, str]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        get_active_trace_id=lambda: None,
        get_last_active_trace_id=lambda thread_local=True: "tr-inactive",
        set_trace_tag=lambda trace_id, key, value: captured_tags.append((trace_id, key, value)),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: SimpleNamespace(active_model_id=None),
            flush_mlflow_traces=lambda: None,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    with mlflow_request_context(
        MlflowTraceRequestContext(
            client_request_id="chat-123",
            session_id="workspace:user:session",
            user_id="user",
            metadata={"fleet_rlm.routing_decision": "url_document_rlm"},
        )
    ):
        trace_id = mlflow_context.update_current_mlflow_trace(
            trace_metadata={"fleet_rlm.source_url": "https://dspy.ai"}
        )

    assert trace_id == "tr-inactive"
    assert ("tr-inactive", "fleet_rlm.client_request_id", "chat-123") in captured_tags
    assert ("tr-inactive", "fleet_rlm.routing_decision", "url_document_rlm") in captured_tags
    assert ("tr-inactive", "fleet_rlm.source_url", "https://dspy.ai") in captured_tags


def test_update_current_mlflow_trace_resolves_completed_trace_by_client_request_id(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context, mlflow_traces
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured_tags: list[tuple[str, str, str]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        get_active_trace_id=lambda: None,
        get_last_active_trace_id=lambda thread_local=True: None,
        set_trace_tag=lambda trace_id, key, value: captured_tags.append((trace_id, key, value)),
    )
    fake_config = SimpleNamespace(active_model_id=None)
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: fake_config,
            flush_mlflow_traces=lambda: None,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )
    monkeypatch.setattr(
        mlflow_traces,
        "resolve_trace_by_client_request_id",
        lambda client_request_id, config, max_results: SimpleNamespace(
            info=SimpleNamespace(trace_id="tr-resolved", client_request_id=client_request_id)
        ),
    )

    with mlflow_request_context(
        MlflowTraceRequestContext(
            client_request_id="chat-456",
            session_id="workspace:user:session",
            user_id="user",
        )
    ):
        trace_id = mlflow_context.update_current_mlflow_trace(trace_metadata={"fleet_rlm.trajectory_steps": "1"})

    assert trace_id == "tr-resolved"
    assert ("tr-resolved", "fleet_rlm.client_request_id", "chat-456") in captured_tags
    assert ("tr-resolved", "fleet_rlm.trajectory_steps", "1") in captured_tags


def test_mlflow_request_context_reapplies_final_metadata_after_flush(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context, mlflow_traces
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured_tags: list[tuple[str, str, str]] = []
    flush_count = 0

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        get_active_trace_id=lambda: None,
        get_last_active_trace_id=lambda thread_local=True: None,
        set_trace_tag=lambda trace_id, key, value: captured_tags.append((trace_id, key, value)),
    )

    def flush_mlflow_traces() -> None:
        nonlocal flush_count
        flush_count += 1

    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: SimpleNamespace(active_model_id=None),
            flush_mlflow_traces=flush_mlflow_traces,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    def resolve_after_first_flush(client_request_id: str, config: Any, max_results: int) -> Any | None:
        _ = config, max_results
        if flush_count == 0:
            return None
        return SimpleNamespace(info=SimpleNamespace(trace_id="tr-after-flush", client_request_id=client_request_id))

    monkeypatch.setattr(mlflow_traces, "resolve_trace_by_client_request_id", resolve_after_first_flush)

    with mlflow_request_context(MlflowTraceRequestContext(client_request_id="chat-789")):
        mlflow_context.update_current_mlflow_trace(
            response_preview="final",
            trace_metadata={"fleet_rlm.routing_decision": "url_document_rlm"},
        )

    assert flush_count == 2
    assert ("tr-after-flush", "fleet_rlm.client_request_id", "chat-789") in captured_tags
    assert ("tr-after-flush", "fleet_rlm.routing_decision", "url_document_rlm") in captured_tags


def test_mlflow_request_context_opens_application_turn_span(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.integrations.observability.mlflow_context import (
        MlflowTraceRequestContext,
        mlflow_request_context,
    )

    captured: list[dict[str, object]] = []

    class FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, object] | None) -> None:
            self.record: dict[str, object] = {
                "name": name,
                "span_type": span_type,
                "attributes": attributes or {},
            }

        def __enter__(self) -> "FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: object) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: object) -> None:
            self.record["outputs"] = outputs

    fake_mlflow = SimpleNamespace(
        get_current_active_span=object,
        get_active_trace_id=lambda: "tr-parent",
        update_current_trace=lambda **kwargs: None,
        start_span=lambda name, span_type=None, attributes=None: FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            get_mlflow_config=lambda: SimpleNamespace(active_model_id=None),
            flush_mlflow_traces=lambda: None,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    with mlflow_request_context(
        MlflowTraceRequestContext(
            client_request_id="chat-parent",
            request_preview="analyze docs",
            final_response_preview="done",
        )
    ):
        pass

    assert captured[0]["name"] == "fleet_rlm.chat_turn"
    assert captured[0]["inputs"] == {"message": "analyze docs"}
    assert captured[0]["outputs"] == {"response": "done"}


def test_record_rlm_trajectory_spans_materializes_repl_steps(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context

    captured: list[dict[str, Any]] = []

    class FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
            self.record = {"name": name, "span_type": span_type, "attributes": attributes or {}, "status": "OK"}

        def __enter__(self) -> "FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: Any) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: Any) -> None:
            self.record["outputs"] = outputs

        def set_status(self, status: str) -> None:
            self.record["status"] = status

    fake_mlflow = SimpleNamespace(
        get_current_active_span=object,
        start_span=lambda name, span_type=None, attributes=None: FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    recorded = mlflow_context.record_rlm_trajectory_spans(
        [
            {
                "reasoning": "Inspect the document text.",
                "code": "print(document_text[:100])",
                "output": "DSPy docs",
            }
        ]
    )

    assert recorded == 1
    assert captured[0]["name"] == "rlm_available_tools"
    assert captured[0]["span_type"] == "LLM"
    assert captured[0]["inputs"]["tools"][0]["function"]["name"] == "repl_execute"
    assert "repl_execute" in captured[0]["attributes"]["mlflow.chat.tools"]
    assert captured[1]["name"] == "repl_execute"
    assert captured[1]["span_type"] == "TOOL"
    assert captured[1]["inputs"]["code"] == "print(document_text[:100])"
    assert captured[1]["outputs"]["output"] == "DSPy docs"
    assert captured[1]["attributes"]["fleet_rlm.trajectory_has_code"] == "true"
    assert captured[1]["status"] == "OK"


def test_record_rlm_trajectory_spans_marks_error_outputs(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context

    captured: list[dict[str, Any]] = []

    class FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
            self.record = {"name": name, "span_type": span_type, "attributes": attributes or {}, "status": "OK"}

        def __enter__(self) -> "FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: Any) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: Any) -> None:
            self.record["outputs"] = outputs

        def set_status(self, status: str) -> None:
            self.record["status"] = status

    fake_mlflow = SimpleNamespace(
        get_current_active_span=object,
        start_span=lambda name, span_type=None, attributes=None: FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    recorded = mlflow_context.record_rlm_trajectory_spans(
        [{"code": "SUBMIT('unterminated)", "output": "[Error] unterminated string literal"}]
    )

    assert recorded == 1
    assert captured[1]["status"] == "ERROR"
    assert captured[1]["attributes"]["fleet_rlm.trajectory_error"] == "true"


def test_record_rlm_trajectory_spans_skips_without_active_trace(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        get_active_trace_id=lambda: None,
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    assert mlflow_context.record_rlm_trajectory_spans([{"code": "print('x')"}]) == 0
