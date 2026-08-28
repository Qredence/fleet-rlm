"""MLflow span contracts for the decomposed Turn.prepare phase (M6)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest


@pytest.fixture
def fleet_trace_active() -> Iterator[None]:
    """Open the fleet turn-trace gate so phase spans engage the (fake) MLflow."""
    from fleet_rlm.observability import tracing as turn_tracing

    token = turn_tracing._fleet_trace_active.set(True)
    yield
    turn_tracing._fleet_trace_active.reset(token)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(start_span_names=[], span_inputs=[], span_outputs=[])

    class _FakeSpan:
        def set_inputs(self, payload: dict[str, object]) -> None:
            calls.span_inputs.append(payload)

        def set_outputs(self, payload: dict[str, object]) -> None:
            calls.span_outputs.append(payload)

    active_span = _FakeSpan()

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        del span_type
        calls.start_span_names.append(name)
        yield active_span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: active_span  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


def _make_turn() -> Any:
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("next"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


def _make_preparer(*, environments: Any = None) -> Any:
    from fleet_rlm.attachments.models import PreparedAttachments
    from fleet_rlm.chat.preparation import DefaultRunPreparer, RunEnvironment
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import RLMExecutionSpec

    class Sink:
        async def remove_private(self, location: str) -> None:
            del location
            return None

    class Attachments:
        async def prepare_run(self, access: Any, ids: Any, run: Any, sink: Any) -> PreparedAttachments:
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self) -> tuple[Any, ...]:
            return ()

        def drain_artifact_candidates(self) -> tuple[Any, ...]:
            return ()

        def drain_memory_candidates(self) -> tuple[Any, ...]:
            return ()

        async def aclose(self) -> None:
            return None

    class CapabilityFactory:
        async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Capabilities:
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    class Environments:
        async def acquire(self, turn: Any, *, deadline: float) -> RunEnvironment:
            del turn
            assert deadline > 0

            async def release() -> None:
                return None

            sink = Sink()
            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    return DefaultRunPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=environments if environments is not None else Environments(),
        capabilities=CapabilityFactory(),
    )


@pytest.mark.asyncio
async def test_prepare_emits_decomposed_phase_spans(monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)

    prepared = await _make_preparer().prepare(_make_turn(), deadline=float("inf"))

    assert calls.start_span_names == [
        "Turn.acquire_environment",
        "Turn.stage_attachments",
        "Turn.prepare_capabilities",
    ]
    assert calls.span_outputs[0]["has_interpreter"] is True
    assert calls.span_outputs[0]["has_snapshot_sink"] is False
    assert calls.span_outputs[0]["phase_status"] == "completed"
    assert calls.span_inputs[1] == {"attachment_count": 0}
    assert calls.span_outputs[1]["staged_count"] == 0
    assert calls.span_outputs[1]["staged_bytes"] == 0
    assert calls.span_inputs[2] == {"skill_selection_count": 0}
    assert calls.span_outputs[2]["notice_count"] == 0
    assert calls.span_outputs[2]["phase_status"] == "completed"
    await prepared.aclose()


@pytest.mark.asyncio
async def test_acquire_environment_failure_marks_phase_failed(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    from fleet_rlm.chat.preparation import RunPreparationUnavailableError

    calls = _install_fake_mlflow(monkeypatch)

    class ExplodingEnvironments:
        async def acquire(self, turn: Any, *, deadline: float) -> Any:
            del turn, deadline
            raise RuntimeError("env boom")

    with pytest.raises(RunPreparationUnavailableError):
        await _make_preparer(environments=ExplodingEnvironments()).prepare(_make_turn(), deadline=float("inf"))

    assert calls.start_span_names == ["Turn.acquire_environment"]
    assert calls.span_outputs[0]["phase_status"] == "failed"


@pytest.mark.asyncio
async def test_prepare_without_active_trace_is_noop() -> None:
    """No fake mlflow and no trace gate: preparation succeeds without MLflow."""
    prepared = await _make_preparer().prepare(_make_turn(), deadline=float("inf"))

    assert prepared.execution.session.request == "next"
    await prepared.aclose()
