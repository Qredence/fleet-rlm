"""P38 single-span trace contract and forced-export fail-soft parity.

VAL-RLM-056 / VAL-CROSS-003 evidence for the shadow-only branch: every
engineering lifecycle operation produces exactly one span (no duplicate
duration/exception/Tool/trajectory effect), and a forced callback-export
failure leaves the Turn outcome and product stream unchanged.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from collections import Counter
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.observability import turn_tracing
from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.runtime import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.inputs: Any = None
        self.outputs: Any = None
        self.attributes: Any = None
        self.status: Any = None

    def set_inputs(self, payload: Any) -> None:
        self.inputs = payload

    def set_outputs(self, payload: Any) -> None:
        self.outputs = payload

    def set_attributes(self, payload: Any) -> None:
        self.attributes = payload

    def set_status(self, status: Any) -> None:
        self.status = status


class _FakeSpanContext:
    def __init__(self, span: _FakeSpan) -> None:
        self._span = span

    def __enter__(self) -> _FakeSpan:
        return self._span

    def __exit__(self, *_args: Any) -> None:
        return None


class _FakeMlflow:
    """In-memory MLflow stand-in that records (or fails) span export."""

    def __init__(self, *, fail_export: bool = False) -> None:
        self.fail_export = fail_export
        self.created: list[_FakeSpan] = []
        self._active = _FakeSpan("fleet_turn")

    def get_current_active_span(self) -> _FakeSpan:
        return self._active

    def start_span(self, *, name: str, **_kwargs: Any) -> _FakeSpanContext:
        if self.fail_export:
            raise RuntimeError("forced callback-export failure")
        span = _FakeSpan(name)
        self.created.append(span)
        return _FakeSpanContext(span)

    def update_current_trace(self, **_kwargs: Any) -> None:
        if self.fail_export:
            raise RuntimeError("forced callback-export failure")


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch, *, fail_export: bool) -> _FakeMlflow:
    fake = _FakeMlflow(fail_export=fail_export)
    entities = SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM", TOOL="TOOL"))
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return fake


def _scripted_models() -> RLMModelBundle:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "call the host helper once", "code": "value = helper(value='a')\n_out = value"},
            {"reasoning": "submit the retained value", "code": "SUBMIT(answer=value)"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    return RLMModelBundle(root, sub)


async def _never_cancelled() -> bool:
    return False


async def _run_one_scripted_turn(monkeypatch: pytest.MonkeyPatch, *, fail_export: bool | None) -> Any:
    """Run one deterministic two-iteration Turn; return (events, stream, fake).

    ``fail_export=None`` keeps tracing disabled (the control); otherwise a
    recording or force-failing MLflow fake is installed under an active Turn
    trace gate.
    """
    fake = None
    token = None
    if fail_export is not None:
        fake = _install_fake_mlflow(monkeypatch, fail_export=fail_export)
        token = turn_tracing._fleet_trace_active.set(True)

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    def helper(value: str) -> str:
        return f"done:{value}"

    # Raw Tool only: the runner observes spec tools exactly once.
    spec = RLMExecutionSpec(tools=(dspy.Tool(helper),))
    try:
        context = RLMExecutionContext(
            identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
            session=SessionView(
                request="complete the deterministic single-span proof",
                session_context=SessionContextManifest(uuid4(), 0, 0, ()),
                attachments=(),
                preparation_notices=(),
            ),
            execution=ExecutionRuntime(
                models=_scripted_models(),
                options=RLMOptions(max_iters=2, max_llm_calls=2),
                deadline=time.monotonic() + 30,
                interpreter=interpreter,
                cancellation_requested=_never_cancelled,
            ),
            capabilities=EmptyCapabilities(spec=spec),
        )
        stream = RLMRunner().stream(context)
        events = [event async for event in stream]
    finally:
        interpreter.shutdown()
        if token is not None:
            turn_tracing._fleet_trace_active.reset(token)
    return events, stream, fake


@pytest.mark.asyncio
async def test_each_lifecycle_operation_produces_exactly_one_span(monkeypatch: pytest.MonkeyPatch) -> None:
    events, stream, fake = await _run_one_scripted_turn(monkeypatch, fail_export=False)

    assert stream.outcome is not None and stream.outcome.succeeded
    assert fake is not None
    counts = Counter(span.name for span in fake.created)
    # One span per normalized lifecycle key; no duplicated engineering effect.
    assert counts == {
        "RLM.execute": 1,
        "RLM.root_action": 2,
        "RLM.root_lm": 2,
        "sandbox.execute": 2,
        "tool.helper": 1,
    }
    # Product events are unaffected by tracing: the deterministic vocabulary.
    kinds = [event.kind for event in events]
    assert kinds.count("run.started") == 1
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 0


@pytest.mark.asyncio
async def test_forced_callback_export_failure_leaves_turn_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    control_events, control_stream, _ = await _run_one_scripted_turn(monkeypatch, fail_export=None)
    failed_events, failed_stream, fake = await _run_one_scripted_turn(monkeypatch, fail_export=True)

    # The forced exporter failure creates no span and leaks no exception.
    assert fake is not None
    assert fake.created == []

    # VAL-CROSS-003 normalization note: generated identities and timing-only
    # fields are allowed to differ between the two runs.
    allowed_nondeterministic = {"event_id", "timestamp", "stream_id", "tool_call_id", "duration_ms"}

    def normalize_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): normalize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize_value(item) for item in value]
        return value

    def normalize(events: list[Any]) -> list[tuple[int, str, dict[str, Any]]]:
        rows = []
        for event in events:
            detail = {
                item.name: normalize_value(getattr(event.detail, item.name))
                for item in dataclasses.fields(event.detail)
            }
            for key in allowed_nondeterministic:
                detail.pop(key, None)
            rows.append((event.sequence, event.kind, detail))
        return rows

    assert normalize(failed_events) == normalize(control_events)
    assert failed_stream.outcome is not None and control_stream.outcome is not None
    assert failed_stream.outcome.terminal_status == control_stream.outcome.terminal_status == "completed"
    assert failed_stream.outcome.prediction is not None
    assert failed_stream.outcome.prediction.outputs == control_stream.outcome.prediction.outputs
    assert failed_stream.outcome.usage["iterations"] == control_stream.outcome.usage["iterations"]
