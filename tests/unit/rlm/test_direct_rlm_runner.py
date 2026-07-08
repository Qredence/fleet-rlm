"""Unit tests for the DirectRLMRunner Phase 2B skeleton."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from fleet_rlm.api.events.project_sse import project_sse
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.rlm.errors import DIRECT_RLM_NOT_IMPLEMENTED, MISSING_PLANNER_LM, TURN_CANCELLED
from fleet_rlm.rlm.runner import DirectRLMRunner
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from tests.unit.runtime_services.fakes import StubAgent


async def _collect_events(
    runner: DirectRLMRunner, ctx: ChatExecutionContext, message: str = "hi"
) -> list[RuntimeEvent]:
    return [event async for event in runner.stream(ctx=ctx, message=message)]


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


class TestDirectRLMRunnerSkeleton:
    @pytest.mark.asyncio
    async def test_emits_status_then_structured_error(self, sample_context: ChatExecutionContext) -> None:
        events = await _collect_events(DirectRLMRunner(), _direct_rlm_context(sample_context))

        assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.ERROR]
        assert events[0].payload["phase"] == "direct_rlm_start"
        assert events[1].payload["code"] == DIRECT_RLM_NOT_IMPLEMENTED.code
        assert events[1].text == DIRECT_RLM_NOT_IMPLEMENTED.message

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
            cancel_check: object = None,
        ) -> AsyncIterator[RuntimeEvent]:
            _ = ctx, message, cancel_check
            yield RuntimeEvent.status("override")
            yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="override done")

        runner = DirectRLMRunner(stream_override=_override)
        events = await _collect_events(runner, sample_context)

        assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.DONE]
        assert events[0].text == "override"


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


class TestStreamTurnDirectRLMDispatch:
    @pytest.mark.asyncio
    async def test_stream_turn_dispatches_to_direct_rlm_runner(
        self,
        sample_context: ChatExecutionContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[ChatExecutionContext, str]] = []

        async def _spy_stream(
            self: DirectRLMRunner,
            *,
            ctx: ChatExecutionContext,
            message: str,
            cancel_check: object = None,
        ) -> AsyncIterator[RuntimeEvent]:
            _ = cancel_check
            calls.append((ctx, message))
            yield RuntimeEvent.status("spy")
            yield RuntimeEvent(kind=RuntimeEventKind.ERROR, text="spy error")

        monkeypatch.setattr(DirectRLMRunner, "stream", _spy_stream)

        ctx = _direct_rlm_context(sample_context)
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        events = await _collect_stream_turn_events(ctx, message="dispatch me")

        assert calls == [(ctx, "dispatch me")]
        assert agent.calls == []
        assert [event.kind for event in events] == [RuntimeEventKind.STATUS, RuntimeEventKind.ERROR]

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
