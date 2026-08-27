"""Cross-trace phase metadata and one-way preparation link for one Fleet Run.

One Fleet Run logs two ``fleet_turn`` MLflow roots (preparation, execution).
These tests prove the phase tags distinguish them, the execution trace links
to the preparation trace id, the preparation trace never carries the link,
and no preparation trace id ever leaks into SSE/product events.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from fleet_rlm.observability import tracing


class _FakeSpan:
    def __init__(self, request_id: str) -> None:
        """Initialize a fake span with the specified request ID and empty recording collections."""
        self.request_id = request_id
        self.inputs: list[dict[str, object]] = []
        self.outputs: list[dict[str, object]] = []
        self.statuses: list[str] = []

    def set_inputs(self, payload: dict[str, object]) -> None:
        """Records an input payload for the span.

        Parameters:
            payload (dict[str, object]): Input data associated with the span.
        """
        self.inputs.append(payload)

    def set_outputs(self, payload: dict[str, object]) -> None:
        """Record an output payload for the span.

        Parameters:
            payload (dict[str, object]): Output data to append to the span's recorded outputs.
        """
        self.outputs.append(payload)

    def set_attributes(self, payload: dict[str, object]) -> None:
        """Set the span attributes to the provided payload.

        Parameters:
            payload (dict[str, object]): Attribute names and values to assign.
        """
        self.attributes = payload

    def set_status(self, status: str) -> None:
        """Record a status value for the span."""
        self.statuses.append(status)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Install a fake MLflow module that records spans and trace updates.

    Returns:
        SimpleNamespace: Recorded spans, trace updates, and active span state.
    """
    calls = SimpleNamespace(spans=[], update_kwargs=[], stack=[])

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        """Create and yield a fake tracing span for the duration of a context."""
        del span_type
        span = _FakeSpan(f"tr-span-{len(calls.spans) + 1}")
        calls.spans.append((name, span))
        calls.stack.append(span)
        try:
            yield span
        finally:
            calls.stack.pop()

    def update_current_trace(**kwargs: Any) -> None:
        """Records updates intended for the current trace."""
        calls.update_kwargs.append(kwargs)

    def get_current_active_span() -> Any:
        """
        Return the currently active fake tracing span, if one exists.

        Returns:
            Any: The active span, or `None` when no span is active.
        """
        return calls.stack[-1] if calls.stack else None

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = update_current_trace  # type: ignore[attr-defined]
    mlflow.get_current_active_span = get_current_active_span  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


@pytest.fixture
def _tracing_active(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Temporarily enables tracing with an increased trace-content limit for a test.

    Parameters:
        monkeypatch (pytest.MonkeyPatch): Fixture used to modify tracing configuration.
    """
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 10_000)
    tracing.set_tracing_active_for_tests(True)
    yield
    tracing.set_tracing_active_for_tests(False)


def _trace_root_updates(calls: SimpleNamespace) -> list[dict[str, Any]]:
    """Return the initial tag/metadata updates, one per ``fleet_turn`` root."""
    return [kwargs for kwargs in calls.update_kwargs if "tags" in kwargs]


async def _run_success_turn(
    *,
    prepared_factory: Any,
    tracing_enabled: bool,
    expose_trace_id: bool,
) -> list[Any]:
    """
    Run a successful turn through the coordinator and collect its emitted events.

    Parameters:
        prepared_factory (Any): Factory that creates the prepared run for the turn.
        tracing_enabled (bool): Whether MLflow tracing is enabled.
        expose_trace_id (bool): Whether trace IDs are exposed in emitted events.

    Returns:
        list[Any]: Events emitted during the turn.
    """
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, Status
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    importlib.import_module("fleet_rlm.rlm.result")

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="phase link coordinator",
    )
    run_id = uuid4()

    class Preparation:
        async def prepare(self, _turn: Any, *, deadline: float) -> Any:
            """Create a prepared run for the current session.

            Parameters:
                _turn (Any): The turn associated with the preparation request.

            Returns:
                Any: The prepared run created for the current session.
            """
            del deadline
            return prepared_factory(run_id, session.id)

    class Stream:
        def __init__(self, execution: Any) -> None:
            """Initialize a completed execution fixture with recorded start and running-status events."""
            recorder = EventRecorder(execution.run_id, execution.session_id)
            self._events = iter(
                (
                    recorder.record(RunStarted(delivery="live")),
                    recorder.record(Status("execution", "running")),
                )
            )
            self.outcome = RLMOutcome(
                "completed",
                PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            )

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            """
            Retrieve the next event from the iterator for asynchronous iteration.

            Returns:
                Any: The next event.

            Raises:
                StopAsyncIteration: When no events remain.
            """
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            return None

    class Runner:
        def stream(self, execution: Any) -> Stream:
            """Create a stream for the specified execution.

            Parameters:
                execution (Any): The execution to stream.

            Returns:
                Stream: A stream associated with the execution.
            """
            return Stream(execution)

    coordinator = TurnCoordinator(
        lifecycle=RunLifecycleService(store, max_artifact_bytes=1024),
        preparation=Preparation(),
        runner=Runner(),
        mlflow_tracing_enabled=tracing_enabled,
        mlflow_expose_trace_id=expose_trace_id,
    )
    events = [
        event
        async for event in await coordinator.open(
            OpenTurnCommand(access, session.id, TurnInput("hello"), "idem", run_id)
        )
    ]
    return events


def _real_prepared_run(run_id: Any, session_id: Any) -> Any:
    """
    Create a prepared run with the specified run and session identifiers.
    """
    from fleet_rlm.chat.run_preparation import PreparedRun, _PreparedRunResources

    return PreparedRun(
        execution=cast("Any", SimpleNamespace(run_id=run_id, session_id=session_id)),
        artifact_sink=cast("Any", object()),
        _resources=_PreparedRunResources(()),
    )


def _legacy_prepared_double(run_id: Any, session_id: Any) -> Any:
    """Private-composition-style double predating the internal link field."""

    class Prepared:
        execution = SimpleNamespace(run_id=run_id, session_id=session_id)
        artifact_sink = SimpleNamespace()
        result_snapshot_sink = None
        post_commit_memory_promotion = None

        async def aclose(self) -> None:
            return None

    return Prepared()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_tracing_active")
async def test_execution_trace_links_preparation_trace_id_one_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm.events import RunCompleted, RunStarted

    calls = _install_fake_mlflow(monkeypatch)
    events = await _run_success_turn(
        prepared_factory=_real_prepared_run,
        tracing_enabled=True,
        expose_trace_id=True,
    )

    tagged = _trace_root_updates(calls)
    assert len(tagged) == 2
    preparation_update, execution_update = tagged
    roots = [span for name, span in calls.spans if name == "fleet_turn"]
    assert len(roots) == 2
    preparation_id, execution_id = roots[0].request_id, roots[1].request_id
    assert execution_id != preparation_id

    # Both roots are phase-tagged so MLflow search can identify each.
    assert preparation_update["tags"]["fleet.trace_phase"] == "preparation"
    assert execution_update["tags"]["fleet.trace_phase"] == "execution"

    # One-way link: execution -> preparation only.
    assert "fleet.preparation_trace_id" not in preparation_update["tags"]
    assert execution_update["tags"]["fleet.preparation_trace_id"] == preparation_id
    assert execution_update["metadata"]["fleet.preparation_trace_id"] == preparation_id

    # SSE exposure: events carry the execution trace id, never preparation's.
    start = next(event.detail for event in events if isinstance(event.detail, RunStarted))
    completed = next(event.detail for event in events if isinstance(event.detail, RunCompleted))
    assert start.trace_id == execution_id
    assert completed.trace_id == execution_id
    assert all(getattr(event.detail, "trace_id", None) != preparation_id for event in events)

    # Terminal states still evolve through the existing OK/ERROR path.
    assert calls.update_kwargs[-1] == {"state": "OK"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_tracing_active")
async def test_preparation_link_fail_soft_for_legacy_prepared_double(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    events = await _run_success_turn(
        prepared_factory=_legacy_prepared_double,
        tracing_enabled=True,
        expose_trace_id=True,
    )
    assert events  # The Turn still completes end to end.

    tagged = _trace_root_updates(calls)
    assert len(tagged) == 2
    execution_update = tagged[1]
    # The double has no link field: the Turn still completes and the execution
    # trace simply has no preparation link instead of failing.
    assert execution_update["tags"]["fleet.trace_phase"] == "execution"
    assert "fleet.preparation_trace_id" not in execution_update["tags"]
    assert calls.update_kwargs[-1] == {"state": "OK"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_tracing_active")
async def test_hidden_expose_trace_id_keeps_link_out_of_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.rlm.events import RunCompleted, RunStarted

    calls = _install_fake_mlflow(monkeypatch)
    events = await _run_success_turn(
        prepared_factory=_real_prepared_run,
        tracing_enabled=True,
        expose_trace_id=False,
    )

    tagged = _trace_root_updates(calls)
    assert len(tagged) == 2
    # MLflow-side correlation is internal only; nothing reaches SSE when the
    # operator-facing trace id exposure is disabled.
    execution_update = tagged[1]
    assert execution_update["tags"]["fleet.trace_phase"] == "execution"
    assert "fleet.preparation_trace_id" in execution_update["tags"]
    start = next(event.detail for event in events if isinstance(event.detail, RunStarted))
    completed = next(event.detail for event in events if isinstance(event.detail, RunCompleted))
    assert start.trace_id is None
    assert completed.trace_id is None


@pytest.mark.usefixtures("_tracing_active")
def test_preparation_link_tag_only_records_on_execution_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.observability.turn_tracing import turn_trace

    calls = _install_fake_mlflow(monkeypatch)
    with turn_trace(
        uuid4(),
        uuid4(),
        enabled=True,
        trace_phase="preparation",
        preparation_trace_id="tr-prep-1",
    ):
        pass
    update = _trace_root_updates(calls)[-1]
    # The link is strictly one-way: a preparation root ignores a supplied id.
    assert update["tags"]["fleet.trace_phase"] == "preparation"
    assert "fleet.preparation_trace_id" not in update["tags"]
    assert "fleet.preparation_trace_id" not in update["metadata"]


@pytest.mark.asyncio
async def test_tracing_disabled_records_no_phase_or_link_and_no_sse_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    events = await _run_success_turn(
        prepared_factory=_real_prepared_run,
        tracing_enabled=False,
        expose_trace_id=True,
    )

    # turn_trace short-circuits before touching MLflow when disabled.
    assert calls.spans == []
    assert calls.update_kwargs == []
    assert all(getattr(event.detail, "trace_id", None) is None for event in events)
