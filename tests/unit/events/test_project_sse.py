"""Unit tests for the AI SDK UIMessage v1 SSE projector (project_sse).

Covers all VAL-PROJ-* assertions from the validation contract:
- VAL-PROJ-001 through VAL-PROJ-029
- Every RuntimeEventKind mapping
- Accumulation, ordering, well-formedness, no-drop, payload projections
- Mixed sequences, no tool-input-delta, step lifecycle
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.events.project_sse import project_sse
from fleet_rlm.runtime.events import (
    RuntimeActorContext,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeToolInfo,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


async def _project(events: list[RuntimeEvent], cancel_flag: dict[str, bool] | None = None) -> list[str]:
    """Feed events through ``project_sse()`` and collect all SSE lines."""

    async def _stream() -> AsyncIterator[RuntimeEvent]:
        for ev in events:
            yield ev

    result: list[str] = []
    async for line in project_sse(_stream(), cancel_flag=cancel_flag):
        result.append(line)
    return result


def _parse_payload(line: str) -> dict[str, Any]:
    """Parse the JSON payload from an SSE ``data: {json}\n\n`` line."""
    assert line.startswith("data: "), f"Expected data: prefix, got: {line!r}"
    payload_str = line[len("data: ") :].strip()
    if payload_str == "[DONE]":
        return {"type": "[DONE]"}
    return json.loads(payload_str)


def _assert_line_shape(lines: list[str]) -> None:
    """Assert all lines (except possibly the last [DONE]) are ``data: {json}\n\n``."""
    for i, line in enumerate(lines):
        assert line.startswith("data: "), f"Line {i}: missing 'data: ' prefix: {line!r}"
        assert line.endswith("\n\n"), f"Line {i}: missing double newline terminator: {line!r}"
        payload_str = line[len("data: ") :].strip()
        if payload_str == "[DONE]":
            assert i == len(lines) - 1, f"[DONE] at line {i}, expected final line"
        else:
            json.loads(payload_str)  # assert valid JSON


# ── VAL-PROJ-001: TEXT emits text-start, text-delta, text-end ────────────────


@pytest.mark.asyncio
async def test_text_event_emits_start_delta_end() -> None:
    """VAL-PROJ-001: TEXT event emits text-start, text-delta, text-end."""
    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    lines = await _project([event])

    _assert_line_shape(lines)
    payloads = [_parse_payload(line) for line in lines]

    # One text-start, one text-delta, one text-end, then finish/finish-step/[DONE]
    assert len(payloads) >= 3
    assert payloads[0]["type"] == "text-start"
    assert payloads[1]["type"] == "text-delta"
    assert payloads[1]["delta"] == "hello"
    assert payloads[2]["type"] == "text-end"


# ── VAL-PROJ-002: TEXT deltas accumulate across multiple TEXT events ─────────


@pytest.mark.asyncio
async def test_text_deltas_accumulate() -> None:
    """VAL-PROJ-002: Multiple TEXT events produce one text-start, deltas, one text-end."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="Hel"),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="lo"),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text=" world"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # Find text-start, text-delta(s), text-end
    start_idx = types.index("text-start")
    delta_indices = [i for i, t in enumerate(types) if t == "text-delta"]
    end_idx = types.index("text-end")

    assert len(delta_indices) == 3
    assert start_idx < delta_indices[0]
    assert delta_indices[-1] < end_idx

    # Concatenated deltas should equal "Hello world"
    deltas = [payloads[i]["delta"] for i in delta_indices]
    assert "".join(deltas) == "Hello world"


# ── VAL-PROJ-003: REASONING emits reasoning-start, reasoning-delta, reasoning-end ─


@pytest.mark.asyncio
async def test_reasoning_event_emits_start_delta_end() -> None:
    """VAL-PROJ-003: REASONING event emits reasoning-start, reasoning-delta, reasoning-end."""
    event = RuntimeEvent(kind=RuntimeEventKind.REASONING, text="thinking...")
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    assert len(payloads) >= 3
    assert payloads[0]["type"] == "reasoning-start"
    assert payloads[1]["type"] == "reasoning-delta"
    assert payloads[1]["delta"] == "thinking..."
    assert payloads[2]["type"] == "reasoning-end"


# ── VAL-PROJ-004: REASONING deltas accumulate ────────────────────────────────


@pytest.mark.asyncio
async def test_reasoning_deltas_accumulate() -> None:
    """VAL-PROJ-004: Multiple REASONING events produce one start, deltas, one end."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.REASONING, text="step 1. "),
        RuntimeEvent(kind=RuntimeEventKind.REASONING, text="step 2."),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    start_idx = types.index("reasoning-start")
    delta_indices = [i for i, t in enumerate(types) if t == "reasoning-delta"]
    end_idx = types.index("reasoning-end")

    assert len(delta_indices) == 2
    assert start_idx < delta_indices[0]
    assert delta_indices[-1] < end_idx

    deltas = [payloads[i]["delta"] for i in delta_indices]
    assert "".join(deltas) == "step 1. step 2."


# ── VAL-PROJ-005: TOOL_CALL emits tool-input-start then tool-input-available ─


@pytest.mark.asyncio
async def test_tool_call_emits_start_and_available() -> None:
    """VAL-PROJ-005: TOOL_CALL emits tool-input-start then tool-input-available."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.TOOL_CALL,
        text="calling tool",
        tool=RuntimeToolInfo(tool_name="repl_execute", tool_args={"code": "print(1)"}),
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    start_idx = types.index("tool-input-start")
    available_idx = types.index("tool-input-available")
    assert start_idx < available_idx, "tool-input-start must precede tool-input-available"

    start = payloads[start_idx]
    available = payloads[available_idx]

    assert start["toolCallId"] == available["toolCallId"], "toolCallId must be shared"
    assert available["toolName"] == "repl_execute"
    assert available["input"] == {"code": "print(1)"}


# ── VAL-PROJ-006: Multiple TOOL_CALL events get distinct toolCallIds ────────


@pytest.mark.asyncio
async def test_multiple_tool_calls_distinct_ids() -> None:
    """VAL-PROJ-006: Two TOOL_CALL events produce distinct toolCallIds."""
    events = [
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL, text="call 1", tool=RuntimeToolInfo(tool_name="tool1", tool_args={"a": 1})
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL, text="call 2", tool=RuntimeToolInfo(tool_name="tool2", tool_args={"b": 2})
        ),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    starts = [p for p in payloads if p["type"] == "tool-input-start"]
    availables = [p for p in payloads if p["type"] == "tool-input-available"]

    assert len(starts) == 2
    assert len(availables) == 2

    # Each start/available pair shares a toolCallId.
    for s, a in zip(starts, availables):
        assert s["toolCallId"] == a["toolCallId"]

    # The two pairs have distinct toolCallIds.
    assert starts[0]["toolCallId"] != starts[1]["toolCallId"]


# ── VAL-PROJ-007: TOOL_RESULT emits tool-output-available ────────────────────


@pytest.mark.asyncio
async def test_tool_result_emits_output_available() -> None:
    """VAL-PROJ-007: TOOL_RESULT emits tool-output-available with output."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.TOOL_RESULT, text="42", tool=RuntimeToolInfo(tool_name="repl_execute", tool_output="42")
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    available = next(p for p in payloads if p["type"] == "tool-output-available")

    assert available["toolName"] == "repl_execute"
    assert available["output"] == "42"
    assert "toolCallId" in available


# ── VAL-PROJ-008: TURN_STARTED emits start, start-step, data-agent (ordered) ─


@pytest.mark.asyncio
async def test_turn_started_emits_start_start_step_data_agent() -> None:
    """VAL-PROJ-008: TURN_STARTED emits start, start-step, data-agent in order."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.TURN_STARTED,
        text="started",
        payload={
            "message_id": "msg-123",
            "selected_skills": ["skill1"],
            "available_tools": ["tool1"],
            "execution_mode": "rlm",
            "session_id": "session-1",
            "run_id": "run-1",
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    start_idx = types.index("start")
    start_step_idx = types.index("start-step")
    data_agent_idx = types.index("data-agent")

    assert start_idx < start_step_idx < data_agent_idx, "start < start-step < data-agent ordering violated"

    # start has a messageId
    assert payloads[start_idx]["messageId"] == "msg-123"

    # data-agent carries metadata
    agent = payloads[data_agent_idx]
    assert agent["selected_skills"] == ["skill1"]
    assert agent["available_tools"] == ["tool1"]
    assert agent["execution_mode"] == "rlm"
    assert agent["session_id"] == "session-1"
    assert agent["run_id"] == "run-1"


# ── VAL-PROJ-009: TURN_INPUTS emits data-turn-inputs ────────────────────────


@pytest.mark.asyncio
async def test_turn_inputs_emits_data_turn_inputs() -> None:
    """VAL-PROJ-009: TURN_INPUTS emits data-turn-inputs with rows."""
    rows = [{"label": "Request", "kind": "request", "value": "hello"}]
    event = RuntimeEvent(kind=RuntimeEventKind.TURN_INPUTS, text="inputs", payload={"rows": rows})
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    turn_inputs = next(p for p in payloads if p["type"] == "data-turn-inputs")
    assert turn_inputs["rows"] == rows


# ── VAL-PROJ-010: SANDBOX_EXEC emits data-sandbox-exec ──────────────────────


@pytest.mark.asyncio
async def test_sandbox_exec_emits_data_sandbox_exec() -> None:
    """VAL-PROJ-010: SANDBOX_EXEC emits data-sandbox-exec with fields."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.SANDBOX_EXEC,
        text="exec",
        payload={
            "sandbox_id": "sb-1",
            "stdout_preview": "out",
            "exit_code": 0,
            "duration_ms": 12,
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-sandbox-exec")
    assert part["sandbox_id"] == "sb-1"
    assert part["stdout_preview"] == "out"
    assert part["exit_code"] == 0
    assert part["duration_ms"] == 12


# ── VAL-PROJ-011: RLM_DELEGATE emits data-rlm-delegate ──────────────────────


@pytest.mark.asyncio
async def test_rlm_delegate_emits_data_rlm_delegate() -> None:
    """VAL-PROJ-011: RLM_DELEGATE emits data-rlm-delegate with actor info."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.RLM_DELEGATE,
        text="delegating",
        actor=RuntimeActorContext(depth=1),
        payload={
            "child_sandbox_id": "sb-2",
            "status": "running",
            "output_preview": "working",
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-rlm-delegate")
    assert part["depth"] == 1
    assert part["child_sandbox_id"] == "sb-2"
    assert part["status"] == "running"


# ── VAL-PROJ-012: MLFLOW_SPAN emits data-span ────────────────────────────────


@pytest.mark.asyncio
async def test_mlflow_span_emits_data_span() -> None:
    """VAL-PROJ-012: MLFLOW_SPAN emits data-span with span metadata."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.MLFLOW_SPAN,
        text="span",
        payload={
            "span_id": "sp-1",
            "name": "predict",
            "status": "started",
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-span")
    assert part["span_id"] == "sp-1"
    assert part["name"] == "predict"
    assert part["status"] == "started"


# ── VAL-PROJ-013: STATUS emits data-status ───────────────────────────────────


@pytest.mark.asyncio
async def test_status_emits_data_status() -> None:
    """VAL-PROJ-013: STATUS emits data-status."""
    event = RuntimeEvent(kind=RuntimeEventKind.STATUS, text="running")
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-status")
    assert part["text"] == "running"


# ── VAL-PROJ-014: WARNING emits data-warning ─────────────────────────────────


@pytest.mark.asyncio
async def test_warning_emits_data_warning() -> None:
    """VAL-PROJ-014: WARNING emits data-warning."""
    event = RuntimeEvent(kind=RuntimeEventKind.WARNING, text="low budget")
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-warning")
    assert part["text"] == "low budget"


# ── VAL-PROJ-015: CLARIFICATION emits data-clarification ─────────────────────


@pytest.mark.asyncio
async def test_clarification_emits_data_clarification() -> None:
    """VAL-PROJ-015: CLARIFICATION emits data-clarification with question/options."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.CLARIFICATION,
        text="Which repo?",
        payload={
            "question": "Which repo?",
            "options": ["a", "b"],
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    part = next(p for p in payloads if p["type"] == "data-clarification")
    assert part["question"] == "Which repo?"
    assert part["options"] == ["a", "b"]


# ── VAL-PROJ-016: SSE lines well-formed (data: {json}\n\n) ───────────────────


@pytest.mark.asyncio
async def test_sse_lines_well_formed() -> None:
    """VAL-PROJ-016: Every line is well-formed data: {json}\n\n."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello"),
        RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)  # This inline checks the well-formedness


# ── VAL-PROJ-017: DONE emits finish-step, finish, [DONE] ─────────────────────


@pytest.mark.asyncio
async def test_done_emits_finish_step_finish_done() -> None:
    """VAL-PROJ-017: DONE event emits finish-step, finish, then [DONE]."""
    event = RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # We need a preceding TURN_STARTED for start-step, otherwise just finish/finish-step
    # Actually VAL-PROJ-017 expects: Given DONE, emits finish-step, finish, then [DONE]
    # Without a start-step, finish-step should still work.
    assert "finish-step" in types
    assert "finish" in types
    finish_step_idx = types.index("finish-step")
    finish_idx = types.index("finish")
    assert finish_step_idx < finish_idx, "finish-step must precede finish"
    assert payloads[-1]["type"] == "[DONE]", "Final line must be [DONE]"


# ── VAL-PROJ-018: ERROR emits error, [DONE] (no finish) ──────────────────────


@pytest.mark.asyncio
async def test_error_emits_error_done_no_finish() -> None:
    """VAL-PROJ-018: ERROR emits error then [DONE]; no finish part."""
    event = RuntimeEvent(kind=RuntimeEventKind.ERROR, text="boom")
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    error_part = next(p for p in payloads if p["type"] == "error")
    assert error_part["text"] == "boom"

    assert "error" in types
    assert "[DONE]" in types
    assert "finish" not in types, "ERROR must not emit finish part"
    assert payloads[-1]["type"] == "[DONE]"


# ── VAL-PROJ-019: Cancel emits abort, [DONE] (no finish/error) ───────────────


@pytest.mark.asyncio
async def test_cancel_emits_abort_done() -> None:
    """VAL-PROJ-019: Cancel signal emits abort then [DONE]; no finish/error."""
    cancel_flag: dict[str, bool] = {"cancelled": True}
    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    lines = await _project([event], cancel_flag=cancel_flag)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert "abort" in types, "Expected abort part on cancellation"
    assert payloads[-1]["type"] == "[DONE]"
    assert "finish" not in types, "Cancel must not emit finish"
    assert "error" not in types, "Cancel must not emit error"


# ── VAL-PROJ-020: Terminal events always followed by [DONE] ──────────────────


@pytest.mark.asyncio
async def test_terminal_events_followed_by_done() -> None:
    """VAL-PROJ-020: All terminal events have [DONE] as the final line."""
    # Test DONE terminal
    lines_done = await _project([RuntimeEvent(kind=RuntimeEventKind.DONE)])
    assert _parse_payload(lines_done[-1])["type"] == "[DONE]"

    # Test ERROR terminal
    lines_error = await _project([RuntimeEvent(kind=RuntimeEventKind.ERROR, text="err")])
    assert _parse_payload(lines_error[-1])["type"] == "[DONE]"

    # Test Cancel terminal
    lines_cancel = await _project(
        [RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")],
        cancel_flag={"cancelled": True},
    )
    assert _parse_payload(lines_cancel[-1])["type"] == "[DONE]"


# ── VAL-PROJ-021: No RuntimeEventKind silently dropped (parametrized) ────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, expected_part_type",
    [
        (RuntimeEventKind.TEXT, "text-start"),
        (RuntimeEventKind.REASONING, "reasoning-start"),
        (RuntimeEventKind.TOOL_CALL, "tool-input-start"),
        (RuntimeEventKind.TOOL_RESULT, "tool-output-available"),
        (RuntimeEventKind.TURN_STARTED, "start"),
        (RuntimeEventKind.TURN_INPUTS, "data-turn-inputs"),
        (RuntimeEventKind.SANDBOX_EXEC, "data-sandbox-exec"),
        (RuntimeEventKind.RLM_DELEGATE, "data-rlm-delegate"),
        (RuntimeEventKind.MLFLOW_SPAN, "data-span"),
        (RuntimeEventKind.STATUS, "data-status"),
        (RuntimeEventKind.WARNING, "data-warning"),
        (RuntimeEventKind.CLARIFICATION, "data-clarification"),
        (RuntimeEventKind.DONE, "finish"),
        (RuntimeEventKind.ERROR, "error"),
    ],
)
async def test_every_kind_mapped(kind: RuntimeEventKind, expected_part_type: str) -> None:
    """VAL-PROJ-021: Every RuntimeEventKind produces at least one SSE part."""
    if kind == RuntimeEventKind.TEXT:
        event = RuntimeEvent(kind=kind, text="hello")
    elif kind == RuntimeEventKind.REASONING:
        event = RuntimeEvent(kind=kind, text="thinking")
    elif kind == RuntimeEventKind.TOOL_CALL:
        event = RuntimeEvent(kind=kind, text="call", tool=RuntimeToolInfo(tool_name="tool", tool_args={}))
    elif kind == RuntimeEventKind.TOOL_RESULT:
        event = RuntimeEvent(kind=kind, text="result", tool=RuntimeToolInfo(tool_name="tool", tool_output="out"))
    elif kind == RuntimeEventKind.TURN_STARTED:
        event = RuntimeEvent(kind=kind, text="start", payload={"message_id": "msg-1"})
    elif kind == RuntimeEventKind.TURN_INPUTS:
        event = RuntimeEvent(kind=kind, text="inputs", payload={"rows": []})
    elif kind == RuntimeEventKind.SANDBOX_EXEC:
        event = RuntimeEvent(kind=kind, text="exec", payload={"sandbox_id": "sb-1"})
    elif kind == RuntimeEventKind.RLM_DELEGATE:
        event = RuntimeEvent(
            kind=kind, text="del", actor=RuntimeActorContext(depth=1), payload={"child_sandbox_id": "sb-2"}
        )
    elif kind == RuntimeEventKind.MLFLOW_SPAN:
        event = RuntimeEvent(kind=kind, text="span", payload={"span_id": "sp-1"})
    elif kind == RuntimeEventKind.STATUS:
        event = RuntimeEvent(kind=kind, text="status")
    elif kind == RuntimeEventKind.WARNING:
        event = RuntimeEvent(kind=kind, text="warn")
    elif kind == RuntimeEventKind.CLARIFICATION:
        event = RuntimeEvent(kind=kind, text="clarify", payload={"question": "?", "options": []})
    elif kind == RuntimeEventKind.DONE:
        event = RuntimeEvent(kind=kind, text="done")
    elif kind == RuntimeEventKind.ERROR:
        event = RuntimeEvent(kind=kind, text="err")
    else:
        raise ValueError(f"Unexpected kind: {kind}")

    lines = await _project([event])
    _assert_line_shape(lines)
    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert expected_part_type in types, f"Kind {kind.value} should produce a {expected_part_type!r} part, got {types}"


# ── VAL-PROJ-022: data-artifact emitted when payload carries artifact ────────


@pytest.mark.asyncio
async def test_data_artifact_from_payload() -> None:
    """VAL-PROJ-022: data-artifact emitted when payload carries artifact field."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="artifact generated",
        payload={
            "artifact": {
                "title": "out.md",
                "content_type": "text/markdown",
                "path": "/tmp/out.md",
            },
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # Primary mapping still emitted
    assert "data-status" in types
    # Additional data-artifact emitted
    artifact = next(p for p in payloads if p["type"] == "data-artifact")
    assert artifact["title"] == "out.md"


# ── VAL-PROJ-023: data-task emitted when payload carries task ────────────────


@pytest.mark.asyncio
async def test_data_task_from_payload() -> None:
    """VAL-PROJ-023: data-task emitted when payload carries task field."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="task progress",
        payload={
            "task": {
                "label": "compile",
                "status": "running",
                "progress": 0.5,
            },
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert "data-status" in types
    task_part = next(p for p in payloads if p["type"] == "data-task")
    assert task_part["label"] == "compile"
    assert task_part["status"] == "running"
    assert task_part["progress"] == 0.5


# ── VAL-PROJ-024: data-performance emitted when payload carries performance ──


@pytest.mark.asyncio
async def test_data_performance_from_payload() -> None:
    """VAL-PROJ-024: data-performance emitted when payload carries performance."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="perf",
        payload={
            "performance": {
                "total_tokens": 1234,
                "latency_ms": 567,
            },
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    perf = next(p for p in payloads if p["type"] == "data-performance")
    assert perf["total_tokens"] == 1234
    assert perf["latency_ms"] == 567


# ── VAL-PROJ-025: data-suggestion emitted when payload carries suggestions ───


@pytest.mark.asyncio
async def test_data_suggestion_from_payload() -> None:
    """VAL-PROJ-025: data-suggestion emitted when payload carries suggestions."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="suggest",
        payload={
            "suggestions": ["rerun with trace"],
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    suggestion = next(p for p in payloads if p["type"] == "data-suggestion")
    assert suggestion["suggestions"] == ["rerun with trace"]


# ── VAL-PROJ-026: data-* payload projections don't suppress primary mapping ──


@pytest.mark.asyncio
async def test_extra_parts_do_not_suppress_primary() -> None:
    """VAL-PROJ-026: Both primary and data-* parts emitted for same event."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.STATUS,
        text="working",
        payload={
            "artifact": {"title": "out.md", "path": "/tmp/out.md"},
            "suggestions": ["try again"],
        },
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # Both primary and additional parts present
    assert "data-status" in types, "Primary mapping (data-status) must be present"
    assert "data-artifact" in types, "Additional data-artifact must be present"
    assert "data-suggestion" in types, "Additional data-suggestion must be present"


# ── VAL-PROJ-027: Full mixed sequence projects every kind with one [DONE] ────


@pytest.mark.asyncio
async def test_full_mixed_sequence() -> None:
    """VAL-PROJ-027: Mixed sequence covering all kinds, ordered, one [DONE]."""
    events = [
        RuntimeEvent(
            kind=RuntimeEventKind.TURN_STARTED, text="start", payload={"message_id": "msg-1", "selected_skills": ["s1"]}
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TURN_INPUTS, text="inputs", payload={"rows": [{"label": "Req", "value": "hi"}]}
        ),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="Hello "),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="world"),
        RuntimeEvent(kind=RuntimeEventKind.REASONING, text="thinking..."),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL, text="call", tool=RuntimeToolInfo(tool_name="tool1", tool_args={"a": 1})
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_RESULT, text="result", tool=RuntimeToolInfo(tool_name="tool1", tool_output="42")
        ),
        RuntimeEvent(kind=RuntimeEventKind.SANDBOX_EXEC, text="exec", payload={"sandbox_id": "sb-1"}),
        RuntimeEvent(
            kind=RuntimeEventKind.RLM_DELEGATE,
            text="delegate",
            actor=RuntimeActorContext(depth=1),
            payload={"child_sandbox_id": "sb-2"},
        ),
        RuntimeEvent(kind=RuntimeEventKind.MLFLOW_SPAN, text="span", payload={"span_id": "sp-1"}),
        RuntimeEvent(kind=RuntimeEventKind.STATUS, text="working"),
        RuntimeEvent(kind=RuntimeEventKind.WARNING, text="caution"),
        RuntimeEvent(kind=RuntimeEventKind.CLARIFICATION, text="clarify", payload={"question": "Which?"}),
        RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # Every kind represented
    expected_types = {
        "start",
        "start-step",
        "data-agent",
        "data-turn-inputs",
        "text-start",
        "text-delta",
        "text-end",
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "data-sandbox-exec",
        "data-rlm-delegate",
        "data-span",
        "data-status",
        "data-warning",
        "data-clarification",
        "finish-step",
        "finish",
        "[DONE]",
    }
    for expected in expected_types:
        assert expected in types, f"Expected {expected!r} in types, got {types}"

    # Exactly one [DONE] as final line
    assert payloads[-1]["type"] == "[DONE]"
    done_count = sum(1 for p in payloads if p["type"] == "[DONE]")
    assert done_count == 1, "Exactly one [DONE] expected"

    # Content order: text-end before reasoning-start, reasoning-end before tools
    text_end_idx = types.index("text-end")
    reasoning_start_idx = types.index("reasoning-start")
    tool_start_idx = types.index("tool-input-start")

    assert text_end_idx < reasoning_start_idx, "Text should close before reasoning starts"
    assert reasoning_start_idx < tool_start_idx, "Reasoning should close before tools"


# ── VAL-PROJ-028: No tool-input-delta emitted in Phase 1 ─────────────────────


@pytest.mark.asyncio
async def test_no_tool_input_delta() -> None:
    """VAL-PROJ-028: No tool-input-delta emitted in Phase 1."""
    # A sequence with multiple TOOL_CALL events
    events = [
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL, text="call", tool=RuntimeToolInfo(tool_name="tool1", tool_args={"a": 1})
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL, text="call2", tool=RuntimeToolInfo(tool_name="tool2", tool_args={"b": 2})
        ),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    for line in lines:
        if line.startswith("data: "):
            payload_str = line[len("data: ") :].strip()
            if payload_str != "[DONE]":
                payload = json.loads(payload_str)
                assert payload.get("type") != "tool-input-delta", (
                    f"tool-input-delta should not appear in Phase 1: {payload}"
                )


# ── VAL-PROJ-029: finish-step closes the step opened by start-step ───────────


@pytest.mark.asyncio
async def test_step_lifecycle() -> None:
    """VAL-PROJ-029: finish-step closes step opened by start-step; equal counts."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TURN_STARTED, text="start", payload={"message_id": "msg-1"}),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello"),
        RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    start_step_count = types.count("start-step")
    finish_step_count = types.count("finish-step")
    finish_count = types.count("finish")

    assert start_step_count == 1, "Expected exactly one start-step"
    assert finish_step_count == 1, "Expected exactly one finish-step"
    assert finish_count == 1, "Expected exactly one finish"

    assert types.index("start-step") < types.index("finish-step") < types.index("finish"), (
        "Order: start-step < finish-step < finish"
    )


# ── Additional tests for edge cases ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_turn() -> None:
    """A turn with only DONE still emits start+start-step+finish-step+finish+[DONE]."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TURN_STARTED, text="start", payload={"message_id": "msg-1"}),
        RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert "start" in types
    assert "start-step" in types
    assert "finish-step" in types
    assert "finish" in types
    assert "[DONE]" in types
    # No text/reasoning/tool parts
    assert "text-start" not in types
    assert "reasoning-start" not in types
    assert "tool-input-start" not in types


@pytest.mark.asyncio
async def test_text_then_reasoning_interleaving() -> None:
    """Text and reasoning segments close properly when alternating."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="Hello "),
        RuntimeEvent(kind=RuntimeEventKind.REASONING, text="thinking "),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="world"),
        RuntimeEvent(kind=RuntimeEventKind.REASONING, text="more thinking"),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    # Should be: text-start, text-delta("Hello "), text-end,
    #            reasoning-start, reasoning-delta("thinking "), reasoning-end,
    #            text-start, text-delta("world"), text-end,
    #            reasoning-start, reasoning-delta("more thinking"), reasoning-end

    text_starts = [i for i, t in enumerate(types) if t == "text-start"]
    text_ends = [i for i, t in enumerate(types) if t == "text-end"]
    reasoning_starts = [i for i, t in enumerate(types) if t == "reasoning-start"]
    reasoning_ends = [i for i, t in enumerate(types) if t == "reasoning-end"]

    assert len(text_starts) == 2
    assert len(text_ends) == 2
    assert len(reasoning_starts) == 2
    assert len(reasoning_ends) == 2

    # Verify ordering: text1, reasoning1, text2, reasoning2
    assert text_starts[0] < text_ends[0] < reasoning_starts[0]
    assert reasoning_starts[0] < reasoning_ends[0] < text_starts[1]
    assert text_starts[1] < text_ends[1] < reasoning_starts[1]


@pytest.mark.asyncio
async def test_cancel_flag_checked_mid_stream() -> None:
    """Cancel flag checked after each event; emits abort + [DONE]."""
    cancel_flag: dict[str, bool] = {"cancelled": False}

    events = [
        RuntimeEvent(kind=RuntimeEventKind.TURN_STARTED, text="start", payload={"message_id": "msg-1"}),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="some text"),
    ]

    # Set cancel BEFORE the TEXT event so project_sse sees it after processing TEXT.
    async def _stream_with_cancel() -> AsyncIterator[RuntimeEvent]:
        for ev in events:
            if ev.kind == RuntimeEventKind.TEXT:
                cancel_flag["cancelled"] = True
            yield ev

    result: list[str] = []
    async for line in project_sse(_stream_with_cancel(), cancel_flag=cancel_flag):
        result.append(line)

    _assert_line_shape(result)
    payloads = [_parse_payload(line) for line in result]
    types = [p["type"] for p in payloads]

    assert "start" in types
    assert "start-step" in types
    assert "data-agent" in types
    assert "text-start" in types
    assert "text-delta" in types
    # After cancel, should see text-end (flush), then abort + [DONE].
    assert "text-end" in types, "Text segment should close before abort"
    assert "abort" in types, "Expected abort on cancellation"
    assert payloads[-1]["type"] == "[DONE]"
    assert "finish" not in types, "Cancel should not emit finish"


@pytest.mark.asyncio
async def test_multiple_tool_call_with_step_index_correlation() -> None:
    """TOOL_CALL/TOOL_RESULT with step_index correlate via toolCallId."""
    events = [
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL,
            text="call 1",
            tool=RuntimeToolInfo(tool_name="tool1", tool_args={"a": 1}, step_index=0),
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_CALL,
            text="call 2",
            tool=RuntimeToolInfo(tool_name="tool2", tool_args={"b": 2}, step_index=1),
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_RESULT,
            text="result 1",
            tool=RuntimeToolInfo(tool_name="tool1", tool_output="out1", step_index=0),
        ),
        RuntimeEvent(
            kind=RuntimeEventKind.TOOL_RESULT,
            text="result 2",
            tool=RuntimeToolInfo(tool_name="tool2", tool_output="out2", step_index=1),
        ),
    ]
    lines = await _project(events)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    starts = [p for p in payloads if p["type"] == "tool-input-start"]
    availables = [p for p in payloads if p["type"] == "tool-input-available"]
    outputs = [p for p in payloads if p["type"] == "tool-output-available"]

    # Each tool call gets a pair
    assert len(starts) == 2
    assert len(availables) == 2
    assert len(outputs) == 2

    # Verify start/available pairing
    for s, a in zip(starts, availables):
        assert s["toolCallId"] == a["toolCallId"]

    # Verify output references are the same toolCallIds
    # The first tool output should match the first tool call's id
    assert outputs[0]["toolCallId"] == starts[0]["toolCallId"]
    assert outputs[1]["toolCallId"] == starts[1]["toolCallId"]


@pytest.mark.asyncio
async def test_no_events_stream_exhausted_cleanly() -> None:
    """Empty event stream should still produce a clean termination."""
    lines = await _project([])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert "finish" in types
    assert payloads[-1]["type"] == "[DONE]"


@pytest.mark.asyncio
async def test_cancel_before_any_events() -> None:
    """If cancel_flag set before any event, emits abort + [DONE]."""
    cancel_flag: dict[str, bool] = {"cancelled": True}
    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    lines = await _project([event], cancel_flag=cancel_flag)
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    assert "abort" in types
    assert payloads[-1]["type"] == "[DONE]"


@pytest.mark.asyncio
async def test_ordering_within_turn_started() -> None:
    """TURN_STARTED produces start < start-step < data-agent."""
    event = RuntimeEvent(
        kind=RuntimeEventKind.TURN_STARTED,
        text="started",
        payload={"message_id": "msg-1", "session_id": "sess-1"},
    )
    lines = await _project([event])
    _assert_line_shape(lines)

    payloads = [_parse_payload(line) for line in lines]
    types = [p["type"] for p in payloads]

    start_idx = types.index("start")
    start_step_idx = types.index("start-step")
    data_agent_idx = types.index("data-agent")

    assert start_idx < start_step_idx < data_agent_idx


@pytest.mark.asyncio
async def test_repeated_identical_sequences_deterministic() -> None:
    """VAL-SSE-044: Identical event sequences produce identical part-type sequences."""
    events = [
        RuntimeEvent(kind=RuntimeEventKind.TURN_STARTED, text="start", payload={"message_id": "msg-1"}),
        RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello"),
        RuntimeEvent(kind=RuntimeEventKind.DONE, text="done"),
    ]

    lines1 = await _project(events)
    lines2 = await _project(events)

    types1 = [p["type"] for p in [_parse_payload(line) for line in lines1]]
    types2 = [p["type"] for p in [_parse_payload(line) for line in lines2]]

    assert types1 == types2, "Deterministic: type sequences must be identical for identical inputs"
