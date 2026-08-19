"""Native RLM retrieval of older committed Session History."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.sessions.history_tools import SESSION_HISTORY_RESULT_BYTE_BUDGET


@pytest.mark.asyncio
async def test_native_rlm_retrieves_older_content_absent_from_initial_kwargs() -> None:
    from fleet_rlm.chat.session_context import build_session_context_manifest
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess

    older_detail = "project codename is cobalt-orchid"
    history = SessionHistory(
        (
            HistoryMessage("user", older_detail),
            *(HistoryMessage("assistant", f"recent-{index}") for index in range(8)),
        )
    )
    session_id = uuid4()
    manifest = build_session_context_manifest(session_id, 4, history)
    assert all(older_detail not in item.preview for item in manifest.recent)
    (history_tool,) = SessionHistoryToolHost(history).as_tools()

    class Capabilities:
        spec = RLMExecutionSpec(tools=(history_tool,))

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

    class Actions:
        calls = 0

        async def acall(self, **_kwargs):
            self.calls += 1
            return dspy.Prediction(
                reasoning="Retrieve the older committed detail.",
                code=(
                    "page = read_session_history(offset=0, limit=1)\n"
                    "detail = page['messages'][0]['content']\n"
                    "print(detail)\n"
                    "SUBMIT(answer=detail)"
                ),
            )

    class Factory:
        def __init__(self) -> None:
            self.actions = Actions()

        def create(self, **kwargs):
            rlm = RLMFactory().create(**kwargs)
            rlm.generate_action = self.actions
            return rlm

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=session_id, access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="What was the project codename?", session_context=manifest, attachments=(), preparation_notices=()
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_iters=1),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )
    factory = Factory()
    stream = RLMRunner(factory=factory).stream(context)
    _events = [event async for event in stream]

    assert factory.actions.calls == 1
    assert stream.outcome is not None
    assert stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == older_detail


@pytest.mark.asyncio
async def test_native_rlm_continues_history_across_truncated_pages() -> None:
    from fleet_rlm.chat.session_context import build_session_context_manifest
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess

    chunk = "y" * 150_000
    history = SessionHistory(
        (
            HistoryMessage("user", chunk),
            HistoryMessage("assistant", chunk),
            HistoryMessage("user", "final-detail"),
        )
    )
    session_id = uuid4()
    manifest = build_session_context_manifest(session_id, 4, history)
    (history_tool,) = SessionHistoryToolHost(history).as_tools()
    first_page = history_tool(offset=0, limit=20)
    assert first_page["truncated"] is True
    assert first_page["byte_budget"] == SESSION_HISTORY_RESULT_BYTE_BUDGET
    second_page = history_tool(offset=first_page["next_offset"], limit=20)
    assert second_page["messages"][-1]["content"] == "final-detail"

    class Capabilities:
        spec = RLMExecutionSpec(tools=(history_tool,))

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        def drain_memory_candidates(self):
            return ()

    class Actions:
        calls = 0

        async def acall(self, **_kwargs):
            self.calls += 1
            return dspy.Prediction(
                reasoning="Retrieve the final detail across truncated pages.",
                code=(
                    "first = read_session_history(offset=0, limit=20)\n"
                    "second = read_session_history(offset=first['next_offset'], limit=20)\n"
                    "detail = second['messages'][-1]['content']\n"
                    "print(detail)\n"
                    "SUBMIT(answer=detail)"
                ),
            )

    class Factory:
        def __init__(self) -> None:
            self.actions = Actions()

        def create(self, **kwargs):
            rlm = RLMFactory().create(**kwargs)
            rlm.generate_action = self.actions
            return rlm

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=session_id, access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="What is the final detail?", session_context=manifest, attachments=(), preparation_notices=()
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_iters=1),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )
    factory = Factory()
    stream = RLMRunner(factory=factory).stream(context)
    _events = [event async for event in stream]

    assert factory.actions.calls == 1
    assert stream.outcome is not None
    assert stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "final-detail"
