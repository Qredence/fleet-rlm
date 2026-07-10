from __future__ import annotations

from fleet_rlm.observability.recorder import RuntimeTraceRecorder
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm.traces.classifier import classify_span


def test_recorder_observes_incrementally_redacts_and_finalizes_direct_turn(monkeypatch) -> None:
    monkeypatch.delenv("MLFLOW_ENABLED", raising=False)
    recorder = RuntimeTraceRecorder(session_id="session-1", execution_backend="direct_rlm")

    first = recorder.observe(
        RuntimeEvent.status(
            "Working in /home/daytona/memory",
            payload={"token_usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}},
        )
    )
    assert [event.kind for event in first] == [RuntimeEventKind.STATUS]
    assert "/home/daytona/memory" not in first[0].text
    assert recorder.finalized is False

    terminal = recorder.observe(
        RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text="complete",
            payload={"execution_backend": "direct_rlm"},
        )
    )

    assert [event.kind for event in terminal] == [RuntimeEventKind.MLFLOW_SPAN, RuntimeEventKind.DONE]
    assert terminal[0].payload["status"] == "completed"
    assert terminal[0].payload["metadata"]["execution_backend"] == "direct_rlm"
    assert recorder.finalized is True
    assert recorder.record is not None
    assert recorder.record.terminal_kind is RuntimeEventKind.DONE
    assert recorder.record.performance.input_tokens == 3
    assert recorder.record.performance.output_tokens == 2
    assert recorder.record.performance.total_tokens == 5

    after_terminal = recorder.observe(RuntimeEvent.status("late callback"))
    assert [event.kind for event in after_terminal] == [RuntimeEventKind.STATUS]
    assert recorder.record.terminal_kind is RuntimeEventKind.DONE


def test_recorder_preserves_legacy_terminal_shape_without_extra_span(monkeypatch) -> None:
    monkeypatch.delenv("MLFLOW_ENABLED", raising=False)
    recorder = RuntimeTraceRecorder(session_id="session-2", execution_backend="legacy_agent_runtime")

    terminal = recorder.observe(RuntimeEvent(kind=RuntimeEventKind.ERROR, text="safe failure"))

    assert [event.kind for event in terminal] == [RuntimeEventKind.ERROR]
    assert recorder.record is not None
    assert recorder.record.terminal_kind is RuntimeEventKind.ERROR


def test_legacy_and_direct_fixture_turns_have_matching_trace_classification_and_usage(monkeypatch) -> None:
    monkeypatch.delenv("MLFLOW_ENABLED", raising=False)
    legacy = RuntimeTraceRecorder(session_id="session", execution_backend="legacy_agent_runtime")
    direct = RuntimeTraceRecorder(session_id="session", execution_backend="direct_rlm")

    source = RuntimeEvent.status(
        "running",
        payload={"usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}},
    )
    legacy.observe(source)
    direct.observe(source)
    legacy_span = RuntimeEvent.mlflow_span(span_id="legacy", name="turn", status="completed")
    legacy.observe(legacy_span)
    legacy_terminal = legacy.observe(RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"))
    direct_terminal = direct.observe(RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"))

    assert [event.kind for event in legacy_terminal] == [RuntimeEventKind.DONE]
    assert [event.kind for event in direct_terminal] == [RuntimeEventKind.MLFLOW_SPAN, RuntimeEventKind.DONE]
    assert legacy.record is not None
    assert direct.record is not None
    assert legacy.record.performance == direct.record.performance

    legacy_classification = classify_span({"attributes": {"event_kind": legacy_span.payload["event_kind"]}})
    direct_classification = classify_span({"attributes": {"event_kind": direct_terminal[0].payload["event_kind"]}})
    assert legacy_classification.render_kind == direct_classification.render_kind == "mlflow_span"
