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
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

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
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
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
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

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
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
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
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None
    assert not stream.outcome.succeeded
    assert stream.outcome.public_error_message == "Turn output is too large"


@pytest.mark.asyncio
async def test_runner_emits_preloaded_skill_events_before_later_output_failure() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def __init__(self) -> None:
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

        def drain_artifact_candidates(self):
            return ()

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="", trajectory=[])

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
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
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def __init__(self) -> None:
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

        def drain_artifact_candidates(self):
            return ()

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
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
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
    from fleet_rlm.rlm.errors import TurnTerminalError
    from fleet_rlm.rlm.runner import _public_failure_message

    # A parametrized terminal error sets an instance ``public_message``; the
    # runner must honor it (matching sanitize_public_error) instead of reading
    # the class attribute.
    error = TurnTerminalError("custom public message")
    assert _public_failure_message(error) == "custom public message"
    assert str(type(error).public_message) == "Turn failed"


@pytest.mark.asyncio
async def test_stream_closed_before_iteration_synthesizes_cancelled_outcome() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        SessionView,
        TurnIdentity,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

    class Factory:
        def create(self, **_kwargs):
            raise AssertionError("worker factory must not run when the stream is closed early")

    async def not_cancelled() -> bool:
        return False

    loop = asyncio.get_running_loop()
    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
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
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    await stream.aclose()

    # Closing before any iteration must not raise IndexError: synthesize a
    # cancelled outcome matching the GeneratorExit path in ``_generate``.
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.public_error_message == "Turn cancelled"
    assert stream.outcome.usage == {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}
