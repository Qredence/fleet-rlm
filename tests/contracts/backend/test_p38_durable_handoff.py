"""P38 durable handoff contract: one lifecycle finish, no runner settlement.

VAL-RLM-067 evidence through the real TurnCoordinator -> RLMRunner ->
RunLifecycleService composition: a successful typed native output reaches the
lifecycle exactly once with immutable validated outputs, while failed and
cancelled outcomes take the settle path and never a successful handoff. The
runner itself emits no terminal event and performs no Turn commit.
"""

from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.rlm.dspy_contract import PredictionResult
from fleet_rlm.rlm.events import TERMINAL_DETAIL_TYPES, RunCompleted
from fleet_rlm.rlm.outcome import RLMOutcome
from tests.contracts.backend.test_coordinator_runner_failures import _Harness


class _FinishSpy:
    """Wrap a real lifecycle service and record every finish resolution."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self.finish_calls: list[RLMOutcome | Any] = []

    async def finish(self, run: Any, resolution: Any, **kwargs: Any) -> Any:
        self.finish_calls.append(resolution)
        return await self._service.finish(run, resolution, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)


def _install_spy(harness: _Harness) -> _FinishSpy:
    spy = _FinishSpy(harness.lifecycle)
    harness.lifecycle = spy
    return spy


@pytest.mark.asyncio
async def test_successful_native_output_reaches_the_lifecycle_exactly_once() -> None:
    harness = _Harness("native_success")
    spy = _install_spy(harness)

    events = await harness.collect()
    if harness.cleanup_supervisor is not None:
        await harness.cleanup_supervisor.shutdown(drain_seconds=1)

    assert len(spy.finish_calls) == 1
    resolution = spy.finish_calls[0]
    assert isinstance(resolution, RLMOutcome)
    assert resolution.terminal_status == "completed"
    prediction = resolution.prediction
    assert isinstance(prediction, PredictionResult)
    # Immutable validated outputs with the runner's schema identity.
    assert prediction.outputs == {"answer": "10"}
    assert prediction.schema_id == "fleet.default"
    assert prediction.schema_version == "1"
    with pytest.raises(TypeError):
        prediction.outputs["answer"] = "mutated"
    # Usage, Artifact/Memory candidates, and execution details are carried.
    assert resolution.usage["iterations"] >= 1
    assert resolution.artifact_candidates == ()
    assert resolution.memory_candidates == ()
    assert resolution.execution_details

    # The runner never emits a terminal itself; the coordinator owns it.
    assert sum(isinstance(event.detail, TERMINAL_DETAIL_TYPES) for event in events) == 1
    assert isinstance(events[-1].detail, RunCompleted)
    assert harness.cleanup_calls == 1


@pytest.mark.asyncio
async def test_failed_and_cancelled_outcomes_never_produce_a_successful_finish() -> None:
    for mode in ("invalid_output", "malformed_trajectory", "internal_cancel", "timeout"):
        harness = _Harness(mode)  # type: ignore[arg-type]
        spy = _install_spy(harness)

        events = await harness.collect()
        if harness.cleanup_supervisor is not None:
            await harness.cleanup_supervisor.shutdown(drain_seconds=1)

        successful = [
            resolution for resolution in spy.finish_calls if isinstance(resolution, RLMOutcome) and resolution.succeeded
        ]
        assert successful == [], f"{mode} produced a successful lifecycle finish"
        assert not any(isinstance(event.detail, RunCompleted) for event in events), mode
        assert harness.cleanup_calls == 1
