"""Unit tests for the DirectRLMRunner Phase 2B/2C backend."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import dspy
import pytest

from fleet_rlm.api.events.project_sse import project_sse
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.rlm.errors import MISSING_INTERPRETER, MISSING_PLANNER_LM, TURN_CANCELLED
from fleet_rlm.rlm.runner import DirectRLMRunner
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from tests.unit.runtime_services.fakes import StubAgent


class _FakePrediction:
    def __init__(self, *, response: str, trajectory: list[dict[str, Any]] | None = None) -> None:
        self.response = response
        self.trajectory = trajectory or []


class _FakeInterpreter:
    volume_mount_path: str | None = None
    sub_lm: object | None = None


class _AgentWithInterpreter:
    def __init__(self, planner_lm: object) -> None:
        self.interpreter = _FakeInterpreter()
        self.core_memory = ""
        self.history = dspy.History(messages=[])
        self.planner_lm = planner_lm


async def _collect_events(
    runner: DirectRLMRunner,
    ctx: ChatExecutionContext,
    message: str = "hi",
    *,
    agent_runtime: object | None = None,
) -> list[RuntimeEvent]:
    runtime = agent_runtime if agent_runtime is not None else ctx.prepared.planner_lm
    return [
        event
        async for event in runner.stream(
            ctx=ctx,
            message=message,
            agent_runtime=runtime,
        )
    ]


async def _collect_stream_turn_events(ctx: ChatExecutionContext, message: str = "hi") -> list[RuntimeEvent]:
    return [event async for event in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message=message)]


async def _project_lines(events: list[RuntimeEvent]) -> list[str]:
    async def _stream() -> AsyncIterator[RuntimeEvent]:
        for event in events:
            yield event

    lines: list[str] = []
    async for line in project_sse(_stream()):
        lines.append(line)
    return lines


def _parse_payload(line: str) -> dict[str, object] | str:
    body = line.removeprefix("data: ").strip()
    if body == "[DONE]":
        return "[DONE]"
    return json.loads(body)


def _direct_rlm_context(
    sample_context: ChatExecutionContext,
    **overrides: object,
) -> ChatExecutionContext:
    controls = overrides.pop("controls", TurnControls(execution_backend=ExecutionBackend.direct_rlm))
    return replace(sample_context, controls=controls, **overrides)


def _fake_turn_executor(**_kwargs: object) -> _FakePrediction:
    return _FakePrediction(
        response="2+2 equals 4",
        trajectory=[
            {
                "reasoning": "Add the numbers",
                "code": "print(2 + 2)",
                "output": "4",
            }
        ],
    )


class TestDirectRLMRunnerErrors:
    @pytest.mark.asyncio
    async def test_missing_interpreter_emits_structured_error(self, sample_context: ChatExecutionContext) -> None:
        events = await _collect_events(DirectRLMRunner(), _direct_rlm_context(sample_context))

        assert events[0].kind == RuntimeEventKind.STATUS
        assert events[-1].kind == RuntimeEventKind.ERROR
        assert events[-1].payload["code"] == MISSING_INTERPRETER.code

    @pytest.mark.asyncio
    async def test_missing_planner_lm_emits_structured_error(self, sample_context: ChatExecutionContext) -> None:
        prepared = replace(sample_context.prepared, planner_lm=None)
        ctx = _direct_rlm_context(sample_context, prepared=prepared)
        events = await _collect_events(DirectRLMRunner(), ctx)

        assert events[-1].kind == RuntimeEventKind.ERROR
        assert events[-1].payload["code"] == MISSING_PLANNER_LM.code

    @pytest.mark.asyncio
    async def test_cancel_before_start_emits_cancelled_error(self, sample_context: ChatExecutionContext) -> None:
        ctx = _direct_rlm_context(sample_context, cancel_flag={"cancelled": True})
        events = await _collect_events(DirectRLMRunner(), ctx, message="stop")

        assert events[-1].kind == RuntimeEventKind.ERROR
        assert events[-1].payload["code"] == TURN_CANCELLED.code

    @pytest.mark.asyncio
    async def test_stream_override_is_injectable(self, sample_context: ChatExecutionContext) -> None:
        async def _override(
            *,
            ctx: ChatExecutionContext,
            message: str,
            agent_runtime: object = None,
            cancel_check: object = None,
        ) -> AsyncIterator[RuntimeEvent]:
            _ = ctx, message, agent_runtime, cancel_check
            yield RuntimeEvent.status("override")
            yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="override done")

        runner = DirectRLMRunner(stream_override=_override)
        events = await _collect_events(runner, sample_context)

        assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.DONE]
        assert events[0].text == "override"


class TestDirectRLMRunnerGoldenPath:
    @pytest.mark.asyncio
    async def test_injected_executor_emits_text_and_done(self, sample_context: ChatExecutionContext) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        runner = DirectRLMRunner(turn_executor=_fake_turn_executor)
        events = await _collect_events(
            runner,
            _direct_rlm_context(sample_context),
            message="What is 2+2?",
            agent_runtime=agent,
        )

        kinds = [event.kind for event in events]
        assert RuntimeEventKind.STATUS in kinds
        assert RuntimeEventKind.TEXT in kinds
        assert kinds[-1] == RuntimeEventKind.DONE
        assert events[-1].text == "2+2 equals 4"
        assert events[-1].payload["execution_backend"] == "direct_rlm"
        assert events[-1].payload["trajectory"]["steps"]

    @pytest.mark.asyncio
    async def test_trajectory_replays_as_runtime_events(self, sample_context: ChatExecutionContext) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        runner = DirectRLMRunner(turn_executor=_fake_turn_executor)
        events = await _collect_events(
            runner,
            _direct_rlm_context(sample_context),
            agent_runtime=agent,
        )

        tool_calls = [event for event in events if event.kind == RuntimeEventKind.TOOL_CALL]
        tool_results = [event for event in events if event.kind == RuntimeEventKind.TOOL_RESULT]
        assert tool_calls
        assert tool_results
        assert tool_calls[0].tool is not None
        assert tool_calls[0].tool.tool_name == "repl_execute"


class TestDirectRLMRunnerProjection:
    @pytest.mark.asyncio
    async def test_error_events_project_through_sse(self, sample_context: ChatExecutionContext) -> None:
        events = await _collect_events(DirectRLMRunner(), _direct_rlm_context(sample_context))
        lines = await _project_lines(events)
        payloads = [_parse_payload(line) for line in lines]
        types = [payload["type"] if isinstance(payload, dict) else payload for payload in payloads]

        assert "data-status" in types
        assert "error" in types
        assert types[-1] == "[DONE]"
        assert "finish" not in types

    @pytest.mark.asyncio
    async def test_successful_turn_projects_through_sse(self, sample_context: ChatExecutionContext) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        events = await _collect_events(
            DirectRLMRunner(turn_executor=_fake_turn_executor),
            _direct_rlm_context(sample_context),
            agent_runtime=agent,
        )
        lines = await _project_lines(events)
        payloads = [_parse_payload(line) for line in lines]
        types = [payload["type"] if isinstance(payload, dict) else payload for payload in payloads]

        assert "text-start" in types
        assert "text-delta" in types
        assert "finish" in types
        assert types[-1] == "[DONE]"


class TestStreamTurnDirectRLMDispatch:
    @pytest.mark.asyncio
    async def test_stream_turn_dispatches_to_direct_rlm_runner(
        self,
        sample_context: ChatExecutionContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[ChatExecutionContext, str, object | None]] = []

        async def _spy_stream(
            self: DirectRLMRunner,
            *,
            ctx: ChatExecutionContext,
            message: str,
            agent_runtime: object | None = None,
            cancel_check: object = None,
        ) -> AsyncIterator[RuntimeEvent]:
            _ = cancel_check
            calls.append((ctx, message, agent_runtime))
            yield RuntimeEvent.status("spy")
            yield RuntimeEvent(kind=RuntimeEventKind.ERROR, text="spy error")

        monkeypatch.setattr(DirectRLMRunner, "stream", _spy_stream)

        ctx = _direct_rlm_context(sample_context)
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        events = await _collect_stream_turn_events(ctx, message="dispatch me")

        assert calls[0][0] is ctx
        assert calls[0][1] == "dispatch me"
        assert calls[0][2] is agent
        assert agent.calls == []
        assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.ERROR]

    @pytest.mark.asyncio
    async def test_stream_turn_runs_injected_direct_rlm_turn(
        self,
        sample_context: ChatExecutionContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        ctx = _direct_rlm_context(sample_context)

        class _InjectedRunner(DirectRLMRunner):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(turn_executor=_fake_turn_executor, **kwargs)

        monkeypatch.setattr("fleet_rlm.rlm.runner.DirectRLMRunner", _InjectedRunner)

        events = [
            event
            async for event in stream_turn(
                ctx=ctx,
                agent_runtime=agent,
                message="What is 2+2?",
            )
        ]

        assert events[-1].kind == RuntimeEventKind.DONE
        assert events[-1].text == "2+2 equals 4"

    @pytest.mark.asyncio
    async def test_legacy_backend_unchanged_when_default(self, sample_context: ChatExecutionContext) -> None:
        events = await _collect_stream_turn_events(sample_context, message="hello")
        agent = sample_context.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        assert len(events) >= 2
        assert events[0].kind == RuntimeEventKind.STATUS
        assert events[-1].kind == RuntimeEventKind.DONE
        assert agent.captured_kwargs is not None
        assert agent.captured_kwargs["message"] == "hello"
