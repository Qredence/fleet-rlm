"""Unit contracts for fail-soft Turn-rooted MLflow spans."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import httpx
import pytest

from fleet_rlm.observability import turn_tracing
from fleet_rlm.observability.turn_tracing import (
    annotate_trace_io,
    current_turn_trace_id,
    start_turn_span,
    turn_phase_span,
    turn_trace,
)
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


@pytest.fixture(autouse=True)
def _activate_fleet_trace_context() -> Iterator[None]:
    """Phase spans gate on an active fleet_turn trace; tests exercise span logic directly."""
    token = turn_tracing._fleet_trace_active.set(True)
    yield
    turn_tracing._fleet_trace_active.reset(token)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    explode: bool = False,
    teardown_explode: bool = False,
) -> SimpleNamespace:
    calls = SimpleNamespace(
        start_span_names=[],
        update_kwargs=[],
        get_trace_calls=0,
        span_inputs=[],
        span_outputs=[],
        span_statuses=[],
    )

    class _FakeSpan:
        request_id = "tr-from-span"

        def __init__(self) -> None:
            self.status: str | None = None

        def set_inputs(self, payload: dict[str, object]) -> None:
            calls.span_inputs.append(payload)

        def set_outputs(self, payload: dict[str, object]) -> None:
            calls.span_outputs.append(payload)

        def set_status(self, status: str) -> None:
            self.status = status
            calls.span_statuses.append(status)

    active_span = _FakeSpan()

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        del span_type
        if explode:
            raise RuntimeError("span boom")
        calls.start_span_names.append(name)
        yield active_span
        if teardown_explode:
            raise RuntimeError("span teardown boom")

    def update_current_trace(**kwargs: Any) -> None:
        calls.update_kwargs.append(kwargs)

    def get_last_active_trace_id(**_kwargs: Any) -> str:
        calls.get_trace_calls += 1
        return "tr-active-123"

    def get_current_active_span() -> _FakeSpan:
        return active_span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = update_current_trace  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = get_last_active_trace_id  # type: ignore[attr-defined]
    mlflow.get_current_active_span = get_current_active_span  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


def test_turn_trace_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("mlflow must not be used when disabled")

    mlflow = ModuleType("mlflow")
    mlflow.start_span = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)

    session_id = uuid4()
    run_id = uuid4()
    with turn_trace(session_id, run_id, enabled=False) as handle:
        assert handle.trace_id is None
        assert current_turn_trace_id() is None


def test_turn_trace_enabled_sets_tags_and_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    session_id = uuid4()
    run_id = uuid4()
    with turn_trace(session_id, run_id, enabled=True) as handle:
        assert handle.trace_id == "tr-active-123"
        assert current_turn_trace_id() == "tr-active-123"
        assert calls.start_span_names == ["fleet_turn"]
        assert calls.update_kwargs == [
            {
                "session_id": str(session_id),
                "user": "fleet-local",
                "tags": {
                    "fleet.run_id": str(run_id),
                    "fleet.session_id": str(session_id),
                },
                "metadata": {
                    "fleet.run_id": str(run_id),
                    "fleet.app_version": turn_tracing._FLEET_APP_VERSION,
                },
            }
        ]
    assert current_turn_trace_id() is None


def test_observed_url_tool_is_nested_under_turn_root_with_bounded_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    observed: list[Any] = []

    def fetch_url(url: str) -> dict[str, object]:
        del url
        return {"content": "private source body", "cache_hit": False}

    source = dspy.Tool(
        fetch_url,
        name="fetch_url",
    )
    wrapped = observe_tool(
        source,
        observed.append,
        ToolEventView(
            input_projection=lambda _arguments: {"source_id": "source-1"},
            output_projection=lambda result: {"cache_hit": result["cache_hit"]},
        ),
    )

    with turn_trace(uuid4(), uuid4(), enabled=True):
        assert wrapped.func(url="https://example.com/report")["content"] == "private source body"

    assert calls.start_span_names == ["fleet_turn", "tool.fetch_url"]
    assert calls.span_inputs[-1]["input"] == {"source_id": "source-1"}
    assert calls.span_outputs[-1]["output"] == {"cache_hit": False}
    assert "private source body" not in str(calls.span_inputs + calls.span_outputs)
    assert "private source body" not in str(observed)


def test_daytona_broker_preserves_batched_tool_span_under_turn_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    calls = _install_fake_mlflow(monkeypatch)
    observed: list[Any] = []

    def llm_query_batched(prompts: list[str]) -> list[str]:
        return [f"result-{index}" for index, _prompt in enumerate(prompts)]

    wrapped = observe_tool(
        dspy.Tool(llm_query_batched, name="llm_query_batched"),
        observed.append,
        ToolEventView(
            input_projection=lambda arguments: {
                "prompt_count": len(arguments["prompts"]),
                "prompt_chars": sum(len(prompt) for prompt in arguments["prompts"]),
            },
            output_projection=lambda result: {"result_count": len(result)},
        ),
    )
    broker = DaytonaHttpToolBroker(sandbox=SimpleNamespace())
    broker._broker_url = "http://example.test"
    broker._broker_secret = "secret"
    pending = [
        {
            "id": "batch-1",
            "lease_token": "lease",
            "tool_name": "llm_query_batched",
            "args": [["alpha evidence", "beta evidence"]],
            "kwargs": {},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pending
        if request.url.path == "/pending":
            result, pending = pending, []
            return httpx.Response(200, json={"requests": result})
        return httpx.Response(200, json={})

    broker._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )

    def execute(name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        assert name == "llm_query_batched"
        return wrapped.func(*args, **kwargs)

    with turn_trace(uuid4(), uuid4(), enabled=True):
        assert broker._poll_once(execute) is True

    assert calls.start_span_names == ["fleet_turn", "tool.llm_query_batched"]
    assert calls.span_inputs[-1]["input"] == {"prompt_count": 2, "prompt_chars": 27}
    assert calls.span_outputs[-1]["output"] == {"result_count": 2}
    assert "alpha evidence" not in str(calls.span_inputs + calls.span_outputs)


def test_turn_trace_span_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, explode=True)
    with turn_trace(uuid4(), uuid4(), enabled=True) as handle:
        assert handle.trace_id is None


def test_turn_trace_preserves_managed_body_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch)
    expected = ValueError("original turn failure")

    with pytest.raises(ValueError) as raised, turn_trace(uuid4(), uuid4(), enabled=True):
        raise expected

    assert raised.value is expected
    assert current_turn_trace_id() is None


def test_turn_trace_teardown_failure_does_not_change_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, teardown_explode=True)

    with turn_trace(uuid4(), uuid4(), enabled=True) as handle:
        assert handle.trace_id == "tr-active-123"

    assert current_turn_trace_id() is None


def test_turn_trace_teardown_failure_preserves_body_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, teardown_explode=True)
    expected = LookupError("turn body failed")

    with pytest.raises(LookupError) as raised, turn_trace(uuid4(), uuid4(), enabled=True):
        raise expected

    assert raised.value is expected
    assert current_turn_trace_id() is None


def test_turn_trace_respects_expose_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch)
    with turn_trace(uuid4(), uuid4(), enabled=True, expose_trace_id=False) as handle:
        assert handle.trace_id is None
        assert current_turn_trace_id() is None


def test_annotate_trace_io_updates_trace_request_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    annotate_trace_io(
        request="how are you?",
        response_text="display answer",
        response_outputs={
            "answer": "public answer",
            "final_reasoning": "public reasoning",
            "internal_payload": {"secret": "value"},
        },
    )

    assert calls.span_inputs[-1] == {"request": "how are you?"}
    assert calls.span_outputs[-1] == {
        "answer": "public answer",
        "final_reasoning": "public reasoning",
    }
    assert calls.update_kwargs[-1] == {
        "request_preview": "how are you?",
        "response_preview": "display answer",
    }
    assert "internal_payload" not in calls.span_outputs[-1]


def test_annotate_trace_io_falls_back_to_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    annotate_trace_io(request="hello")

    assert calls.span_inputs[-1] == {"request": "hello"}
    assert calls.span_outputs[-1] == {"answer": ""}
    assert calls.span_statuses == []


def test_annotate_trace_io_marks_root_span_error_when_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    annotate_trace_io(request="q", response_text="Turn failed", failed=True)

    assert calls.span_outputs[-1] == {"answer": "Turn failed"}
    assert calls.span_statuses == ["ERROR"]


def test_turn_phase_span_records_bounded_metadata_when_a_turn_span_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    with turn_phase_span("RLM.execute", inputs={"max_iterations": 20, "max_llm_calls": 50}):
        pass

    assert calls.start_span_names == ["RLM.execute"]
    assert calls.span_inputs[-1] == {"max_iterations": 20, "max_llm_calls": 50}
    assert calls.span_outputs[-1] == {"phase_status": "completed"}


def test_start_turn_span_supports_callback_lifecycles_and_failure_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    handle = start_turn_span("RLM.root_lm", inputs={"role": "root"}, span_type="LLM")
    handle.finish(phase_status="failed", outputs={"failure_category": "timeout"})

    assert calls.start_span_names == ["RLM.root_lm"]
    assert calls.span_inputs[-1] == {"role": "root"}
    assert calls.span_outputs[-1] == {
        "failure_category": "timeout",
        "phase_status": "failed",
    }
    assert calls.span_statuses == ["ERROR"]


def test_turn_phase_span_records_failures_without_suppressing_them(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    with (
        pytest.raises(RuntimeError, match="expected"),
        turn_phase_span("Turn.settlement", inputs={"terminal_status": "failed"}),
    ):
        raise RuntimeError("expected")

    assert calls.span_outputs[-1] == {"phase_status": "failed"}


def test_turn_phase_span_merges_handle_outputs_with_phase_status(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    with turn_phase_span("sandbox.execute", inputs={"iteration": 1}) as phase:
        phase.set_outputs({"stdout_chars": 5, "result_kind": "output"})

    assert calls.span_outputs[-1] == {
        "stdout_chars": 5,
        "result_kind": "output",
        "phase_status": "completed",
    }


def test_turn_phase_span_handle_outputs_survive_body_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    with (
        pytest.raises(RuntimeError, match="boom"),
        turn_phase_span("sandbox.execute", inputs={"iteration": 2}) as phase,
    ):
        phase.set_outputs({"stdout_chars": 3})
        raise RuntimeError("boom")

    assert calls.span_outputs[-1] == {"stdout_chars": 3, "phase_status": "failed"}


def test_turn_phase_span_setup_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, explode=True)
    executed = False

    with turn_phase_span("RLM.execute", inputs={"max_iterations": 20}):
        executed = True

    assert executed


def test_turn_phase_span_without_active_trace_preserves_body_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mlflow(monkeypatch)
    mlflow = sys.modules["mlflow"]
    mlflow.get_current_active_span = lambda: None  # type: ignore[attr-defined]
    expected = ValueError("phase body failed")

    with pytest.raises(ValueError) as raised, turn_phase_span("Turn.prepare", inputs={}):
        raise expected

    assert raised.value is expected
