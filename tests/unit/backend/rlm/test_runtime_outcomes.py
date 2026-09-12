"""RLM runner failure and terminal outcome projection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


@pytest.mark.asyncio
async def test_runner_retains_prediction_usage_when_typed_output_is_invalid() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    prediction = dspy.Prediction(
                        answer="",
                        trajectory=[
                            {"reasoning": "step one", "code": "x=1", "output": "1"},
                            {"reasoning": "step two", "code": "SUBMIT()", "output": "FINAL submitted"},
                        ],
                    )
                    prediction.set_lm_usage({"root": {"prompt_tokens": 9, "completion_tokens": 3}})
                    return prediction

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None
    assert not stream.outcome.succeeded
    assert stream.outcome.public_error_message == "Turn output is invalid"
    assert stream.outcome.usage["iterations"] == 2
    assert stream.outcome.usage["observed_lm_usage"] == {
        "root": {"prompt_tokens": 9, "completion_tokens": 3},
    }


@pytest.mark.asyncio
async def test_runner_reports_turn_output_too_large_for_oversized_answer() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    prediction = dspy.Prediction(
                        answer="x" * 200,
                        trajectory=[
                            {
                                "reasoning": "submit long",
                                "code": "SUBMIT(answer=answer)",
                                "output": "FINAL submitted",
                            },
                        ],
                    )
                    prediction.set_lm_usage({"root": {"prompt_tokens": 2, "completion_tokens": 1}})
                    return prediction

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_output_chars=32),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None
    assert not stream.outcome.succeeded
    assert stream.outcome.public_error_message == "Turn output is too large"


@pytest.mark.asyncio
async def test_runner_emits_preloaded_skill_events_before_later_output_failure() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Capabilities(EmptyCapabilities):
        def __init__(self) -> None:
            super().__init__()
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="", trajectory=[])

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "skill.activated",
        "skill.loaded",
    ]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["cancelled", "timeout"])
async def test_runner_emits_preloaded_skill_events_before_cancel_or_timeout(terminal_status: str) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Capabilities(EmptyCapabilities):
        def __init__(self) -> None:
            super().__init__()
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="late", trajectory=[])

            return Program()

    async def cancellation_probe() -> bool:
        return terminal_status == "cancelled"

    loop = asyncio.get_running_loop()
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=loop.time() - 1 if terminal_status == "timeout" else loop.time() + 10,
            interpreter=None,
            cancellation_requested=cancellation_probe,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events][:4] == [
        "run.started",
        "status",
        "skill.activated",
        "skill.loaded",
    ]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == terminal_status


def test_public_failure_message_honors_instance_override() -> None:
    from fleet_rlm.rlm.result import RunTerminalError
    from fleet_rlm.rlm.runtime import _public_failure_message

    # A parametrized terminal error sets an instance ``public_message``; the
    # runner must honor the instance attribute instead of reading
    # the class attribute.
    error = RunTerminalError("custom public message")
    assert _public_failure_message(error) == "custom public message"
    assert str(type(error).public_message) == "Turn failed"


@pytest.mark.asyncio
async def test_stream_closed_before_iteration_synthesizes_cancelled_outcome() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            raise AssertionError("worker factory must not run when the stream is closed early")

    async def not_cancelled() -> bool:
        return False

    loop = asyncio.get_running_loop()
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=loop.time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    await stream.aclose()

    # Closing before any iteration must not raise IndexError: synthesize a
    # cancelled outcome matching the GeneratorExit path in ``_generate``.
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.public_error_message == "Turn cancelled"
    assert stream.outcome.usage == {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}


def test_delegation_usage_falls_back_to_started_calls_without_executor() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.recursion import DelegationMetrics
    from fleet_rlm.rlm.runtime import _delegation_usage

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_recursive_call()
    metrics.record_recursive_batch()
    context = SimpleNamespace(delegation=SimpleNamespace(metrics=metrics))

    out = _delegation_usage(context)

    assert out["recursive_call_count"] == 1
    assert out["delegation_metrics"]["lm_call_counts"] == [{"role": "root", "recursive_depth": 0, "count": 1}]


def test_delegation_usage_prefers_executor_reserved_count() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.recursion import DelegationMetrics, RecursiveCallSummary
    from fleet_rlm.rlm.runtime import _delegation_usage

    metrics = DelegationMetrics()
    summary = RecursiveCallSummary(
        call_count=5,
        delegated_prompt_chars=0,
        maximum_prompt_chars=0,
        child_iterations=0,
        termination_modes=(),
        delegation_metrics=metrics.snapshot(),
    )
    executor = SimpleNamespace(summary=lambda: summary)
    context = SimpleNamespace(delegation=SimpleNamespace(metrics=metrics))

    out = _delegation_usage(context, executor)

    assert out["recursive_call_count"] == 5


def test_outcome_usage_with_delegation_commits_to_usage_part() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.recursion import DelegationMetrics
    from fleet_rlm.rlm.result import observed_usage
    from fleet_rlm.sessions.committed_turn import UsagePart

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_delegated_input_bytes(64)
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})
    usage = observed_usage(
        prediction,
        duration_ms=7,
        lms=(SimpleNamespace(model="m", history=[{"usage": {"prompt_tokens": 8, "completion_tokens": 2}}]),),
        delegation={"recursive_call_count": 1, "delegation_metrics": metrics.snapshot().as_dict()},
    )

    part = UsagePart(value=usage)

    assert part.value["observed_lm_usage"]["m"]["input_tokens"] == 8
    assert part.value["delegation_metrics"]["delegated_input_bytes"] == 64
    assert tuple(part.value["delegation_metrics"]["lm_call_counts"]) == (
        {"role": "root", "recursive_depth": 0, "count": 1},
    )
    assert part.value["recursive_call_count"] == 1
