"""RuntimeEvent parity tests for opt-in direct_rlm (Phase 2D)."""

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
from fleet_rlm.rlm.runner import DirectRLMRunner
from fleet_rlm.runtime.events import EVENT_SCHEMA_VERSION, RuntimeEvent, RuntimeEventKind
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
        self.core_memory = "session notes"
        self.history = dspy.History(messages=[{"role": "user", "content": "prior"}])
        self.planner_lm = planner_lm


def _direct_rlm_context(sample_context: ChatExecutionContext) -> ChatExecutionContext:
    return replace(
        sample_context,
        controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
    )


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


async def _collect_events(
    ctx: ChatExecutionContext,
    *,
    agent_runtime: object,
    message: str = "What is 2+2?",
) -> list[RuntimeEvent]:
    runner = DirectRLMRunner(turn_executor=_fake_turn_executor)
    return [
        event
        async for event in runner.stream(
            ctx=ctx,
            message=message,
            agent_runtime=agent_runtime,
        )
    ]


async def _project_lines(events: list[RuntimeEvent]) -> list[str]:
    async def _stream() -> AsyncIterator[RuntimeEvent]:
        for event in events:
            yield event

    lines: list[str] = []
    async for line in project_sse(_stream()):
        lines.append(line)
    return lines


class TestDirectRLMEventParity:
    @pytest.mark.asyncio
    async def test_direct_rlm_event_sequence_for_fake_trajectory(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        events = await _collect_events(_direct_rlm_context(sample_context), agent_runtime=agent)

        kinds = [event.kind for event in events]
        assert kinds[0] == RuntimeEventKind.STATUS
        assert kinds[1] == RuntimeEventKind.TURN_INPUTS
        assert RuntimeEventKind.REASONING in kinds
        assert RuntimeEventKind.TOOL_CALL in kinds
        assert RuntimeEventKind.TOOL_RESULT in kinds
        assert RuntimeEventKind.TEXT in kinds
        assert kinds[-1] == RuntimeEventKind.DONE

        turn_inputs_index = kinds.index(RuntimeEventKind.TURN_INPUTS)
        execute_status_index = next(
            index
            for index, event in enumerate(events)
            if event.kind == RuntimeEventKind.STATUS and event.payload.get("phase") == "direct_rlm_execute"
        )
        reasoning_index = kinds.index(RuntimeEventKind.REASONING)
        assert turn_inputs_index < execute_status_index < reasoning_index

        tool_call = next(event for event in events if event.kind == RuntimeEventKind.TOOL_CALL)
        assert tool_call.tool is not None
        assert tool_call.tool.tool_name == "repl_execute"

    @pytest.mark.asyncio
    async def test_direct_rlm_error_sequence_projects_through_sse(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        events = [
            event
            async for event in DirectRLMRunner().stream(
                ctx=_direct_rlm_context(sample_context),
                message="hi",
                agent_runtime=sample_context.prepared.planner_lm,
            )
        ]

        assert events[-1].kind == RuntimeEventKind.ERROR
        lines = await _project_lines(events)
        payloads = []
        for line in lines:
            body = line.removeprefix("data: ").strip()
            if body == "[DONE]":
                payloads.append("[DONE]")
            elif line.startswith("data: "):
                payloads.append(json.loads(body))
        types = [payload["type"] if isinstance(payload, dict) else payload for payload in payloads]
        assert "error" in types
        assert types[-1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_direct_rlm_done_payload_includes_backend_and_trajectory(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        agent = _AgentWithInterpreter(sample_context.prepared.planner_lm)
        events = await _collect_events(_direct_rlm_context(sample_context), agent_runtime=agent)
        done_event = events[-1]

        assert done_event.kind == RuntimeEventKind.DONE
        assert done_event.payload["execution_backend"] == "direct_rlm"
        assert done_event.payload["schema_version"] == EVENT_SCHEMA_VERSION
        assert done_event.payload["history_turns"] == 2
        assert done_event.payload["trajectory"]["steps"]

    @pytest.mark.asyncio
    async def test_legacy_default_path_unchanged(self, sample_context: ChatExecutionContext) -> None:
        events = [
            event
            async for event in stream_turn(
                ctx=sample_context,
                agent_runtime=sample_context.prepared.planner_lm,
                message="hello",
            )
        ]
        agent = sample_context.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        assert events[0].kind == RuntimeEventKind.STATUS
        assert events[-1].kind == RuntimeEventKind.DONE
        assert agent.captured_kwargs is not None
        assert agent.captured_kwargs["message"] == "hello"
        assert RuntimeEventKind.TURN_INPUTS not in [event.kind for event in events]
