"""AI SDK UIMessage v1 SSE projector: RuntimeEvent → SSE data lines.

Projects one or more ``RuntimeEvent`` objects into AI SDK UIMessage v1 parts
over Server-Sent Events (SSE).  Each part is emitted as a ``data: {json}\n\n``
line.  Terminal events (``DONE``, ``ERROR``, cancellation) are followed by
``data: [DONE]\n\n``.

Usage::

    async for line in project_sse(event_stream):
        print(line, end="")

See the mapping table in ``ADR-0003`` for the authoritative kind-to-part mapping.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


def _make_part(part_type: str, fields: dict[str, Any] | None = None) -> str:
    """Format an SSE ``data: {json}\n\n`` line for one AI SDK part."""
    payload: dict[str, Any] = {"type": part_type}
    if fields:
        payload.update(fields)
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _generate_tool_call_id() -> str:
    """Generate a unique tool call identifier for AI SDK parts."""
    return f"call_{uuid4().hex[:16]}"


async def project_sse(
    event_stream: AsyncIterator[RuntimeEvent],
    cancel_flag: dict[str, bool] | None = None,
) -> AsyncIterator[str]:
    """Project ``RuntimeEvent`` objects into AI SDK UIMessage v1 SSE lines.

    This is the SSE counterpart of ``project_chat()``.  It consumes a stream
    of ``RuntimeEvent`` objects (typically from ``stream_turn()``) and yields
    well-formed SSE ``data:`` lines according to the AI SDK Data Stream
    Protocol v1.

    Args:
        event_stream: An async iterator of ``RuntimeEvent`` objects to
            project.  Typically the output of ``stream_turn()``.
        cancel_flag: Optional shared dictionary used to detect client
            cancellation.  When ``cancel_flag.get("cancelled", False)``
            becomes ``True``, the projector emits ``abort`` then
            ``[DONE]`` and stops.

    Yields:
        SSE ``data:`` lines as strings, each terminated by ``\\n``.
        The final line (for terminal events) is ``data: [DONE]\\n\\n``.
    """
    text_active = False
    reasoning_active = False
    step_active = False
    tool_call_counter = 0
    # Map step_index → toolCallId for TOOL_CALL → TOOL_RESULT correlation.
    tool_call_ids: dict[int, str] = {}
    # Fallback list for tool calls without step_index.
    tool_call_ids_ordered: list[str] = []

    def _gen_tool_call_id() -> str:
        nonlocal tool_call_counter
        tid = f"call_{tool_call_counter}_{uuid4().hex[:12]}"
        tool_call_counter += 1
        return tid

    def _flush_text() -> list[str]:
        """Emit text-end if a text segment is open."""
        nonlocal text_active
        if not text_active:
            return []
        text_active = False
        return [_make_part("text-end")]

    def _flush_reasoning() -> list[str]:
        """Emit reasoning-end if a reasoning segment is open."""
        nonlocal reasoning_active
        if not reasoning_active:
            return []
        reasoning_active = False
        return [_make_part("reasoning-end")]

    def _emit_extra_parts(payload: dict[str, Any]) -> list[str]:
        """Emit additional ``data-*`` parts from payload fields.

        These are emitted alongside the primary part mapping and do not
        suppress it.
        """
        lines: list[str] = []

        # data-artifact
        artifact = payload.get("artifact")
        if artifact is not None and isinstance(artifact, dict):
            art_fields: dict[str, Any] = {}
            for k in ("title", "content_type", "path"):
                if k in artifact:
                    art_fields[k] = artifact[k]
            if art_fields:
                lines.append(_make_part("data-artifact", art_fields))

        # data-task
        task = payload.get("task")
        if task is not None and isinstance(task, dict):
            task_fields: dict[str, Any] = {}
            for k in ("label", "status", "progress"):
                if k in task:
                    task_fields[k] = task[k]
            if task_fields:
                lines.append(_make_part("data-task", task_fields))

        # data-performance
        performance = payload.get("performance")
        if performance is not None and isinstance(performance, dict):
            perf_fields: dict[str, Any] = {}
            for k in ("total_tokens", "latency_ms", "cost"):
                if k in performance:
                    perf_fields[k] = performance[k]
            if perf_fields:
                lines.append(_make_part("data-performance", perf_fields))

        # data-suggestion
        suggestions = payload.get("suggestions")
        if suggestions is not None:
            if isinstance(suggestions, list):
                lines.append(_make_part("data-suggestion", {"suggestions": suggestions}))
            elif isinstance(suggestions, dict):
                lines.append(_make_part("data-suggestion", suggestions))

        return lines

    async for event in event_stream:
        kind = event.kind
        lines: list[str] = []
        payload = event.payload or {}

        # ── TEXT ──────────────────────────────────────────────────────────
        if kind == RuntimeEventKind.TEXT:
            # Close any open reasoning segment before starting text.
            lines.extend(_flush_reasoning())
            if not text_active:
                text_active = True
                lines.append(_make_part("text-start"))
            lines.append(_make_part("text-delta", {"delta": event.text}))

        # ── REASONING ─────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.REASONING:
            # Close any open text segment before starting reasoning.
            lines.extend(_flush_text())
            if not reasoning_active:
                reasoning_active = True
                lines.append(_make_part("reasoning-start"))
            lines.append(_make_part("reasoning-delta", {"delta": event.text}))

        # ── TOOL_CALL ────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.TOOL_CALL:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())

            tool_name = (
                event.tool.tool_name
                if event.tool
                else payload.get("tool_name", "")
            )
            tool_input = (
                event.tool.tool_args
                if event.tool and event.tool.tool_args
                else payload.get("tool_args", {})
            )
            step_index = (
                event.tool.step_index
                if event.tool
                else payload.get("step_index")
            )

            tool_call_id = _gen_tool_call_id()
            if step_index is not None:
                tool_call_ids[step_index] = tool_call_id
            else:
                tool_call_ids_ordered.append(tool_call_id)

            lines.append(
                _make_part("tool-input-start", {
                    "toolCallId": tool_call_id,
                    "toolName": tool_name,
                })
            )
            lines.append(
                _make_part("tool-input-available", {
                    "toolCallId": tool_call_id,
                    "toolName": tool_name,
                    "input": tool_input,
                })
            )

        # ── TOOL_RESULT ───────────────────────────────────────────────────
        elif kind == RuntimeEventKind.TOOL_RESULT:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())

            tool_name = (
                event.tool.tool_name
                if event.tool
                else payload.get("tool_name", "")
            )
            tool_output = (
                event.tool.tool_output
                if event.tool
                else payload.get("tool_output")
            )
            step_index = (
                event.tool.step_index
                if event.tool
                else payload.get("step_index")
            )

            # Resolve toolCallId: match by step_index, then fallback to ordered.
            tool_call_id = ""
            if step_index is not None and step_index in tool_call_ids:
                tool_call_id = tool_call_ids.pop(step_index)
            elif tool_call_ids_ordered:
                tool_call_id = tool_call_ids_ordered.pop(0)
            else:
                tool_call_id = _gen_tool_call_id()

            lines.append(
                _make_part("tool-output-available", {
                    "toolCallId": tool_call_id,
                    "toolName": tool_name,
                    "output": tool_output,
                })
            )

        # ── TURN_STARTED ──────────────────────────────────────────────────
        elif kind == RuntimeEventKind.TURN_STARTED:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())

            message_id = payload.get("message_id", str(uuid4()))
            lines.append(_make_part("start", {"messageId": message_id}))

            lines.append(_make_part("start-step"))
            step_active = True

            # data-agent carries fleet metadata.
            agent_fields: dict[str, Any] = {}
            selected_skills = payload.get("selected_skills")
            available_tools = payload.get("available_tools")
            if selected_skills is not None:
                agent_fields["selected_skills"] = selected_skills
            if available_tools is not None:
                agent_fields["available_tools"] = available_tools
            for key in ("execution_mode", "session_id", "run_id"):
                val = payload.get(key)
                if val is not None:
                    agent_fields[key] = val
            lines.append(_make_part("data-agent", agent_fields or None))

        # ── TURN_INPUTS ───────────────────────────────────────────────────
        elif kind == RuntimeEventKind.TURN_INPUTS:
            lines.append(
                _make_part("data-turn-inputs", {
                    "rows": payload.get("rows", []),
                })
            )

        # ── SANDBOX_EXEC ──────────────────────────────────────────────────
        elif kind == RuntimeEventKind.SANDBOX_EXEC:
            sandbox_fields: dict[str, Any] = {}
            for key in (
                "sandbox_id", "command", "code_preview",
                "stdout_preview", "stderr_preview",
                "exit_code", "duration_ms",
            ):
                val = payload.get(key)
                if val is not None:
                    sandbox_fields[key] = val
            lines.append(_make_part("data-sandbox-exec", sandbox_fields or None))

        # ── RLM_DELEGATE ──────────────────────────────────────────────────
        elif kind == RuntimeEventKind.RLM_DELEGATE:
            delegate_fields: dict[str, Any] = {}
            if event.actor is not None:
                delegate_fields["depth"] = event.actor.depth
                delegate_fields["actor"] = event.actor.model_dump(mode="json")
            for key in ("child_sandbox_id", "status", "output_preview"):
                val = payload.get(key)
                if val is not None:
                    delegate_fields[key] = val
            if "depth" not in delegate_fields and "depth" in payload:
                delegate_fields["depth"] = payload["depth"]
            lines.append(_make_part("data-rlm-delegate", delegate_fields or None))

        # ── MLFLOW_SPAN ───────────────────────────────────────────────────
        elif kind == RuntimeEventKind.MLFLOW_SPAN:
            span_fields: dict[str, Any] = {}
            for key in ("span_id", "name", "status", "trace_id", "parent_span_id", "duration_ms"):
                val = payload.get(key)
                if val is not None:
                    span_fields[key] = val
            lines.append(_make_part("data-span", span_fields or None))

        # ── STATUS ────────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.STATUS:
            lines.append(
                _make_part("data-status", {
                    "text": event.text,
                    **(payload if payload else {}),
                })
            )

        # ── WARNING ───────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.WARNING:
            lines.append(
                _make_part("data-warning", {
                    "text": event.text,
                })
            )

        # ── CLARIFICATION ─────────────────────────────────────────────────
        elif kind == RuntimeEventKind.CLARIFICATION:
            lines.append(
                _make_part("data-clarification", {
                    "question": payload.get("question", event.text),
                    "options": payload.get("options", []),
                })
            )

        # ── DONE ──────────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.DONE:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())
            # Always emit finish-step on DONE even without a preceding
            # TURN_STARTED (VAL-PROJ-017, VAL-PROJ-029).
            lines.append(_make_part("finish-step"))
            step_active = False
            lines.append(_make_part("finish"))
            for line in lines:
                yield line
            yield "data: [DONE]\n\n"
            return

        # ── ERROR ─────────────────────────────────────────────────────────
        elif kind == RuntimeEventKind.ERROR:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())
            lines.append(
                _make_part("error", {
                    "text": event.text,
                })
            )
            for line in lines:
                yield line
            yield "data: [DONE]\n\n"
            return

        # ── Unknown kind (defensive) ──────────────────────────────────────
        else:
            lines.extend(_flush_text())
            lines.extend(_flush_reasoning())

        # Yield primary mapping lines.
        for line in lines:
            yield line

        # Yield additional data-* parts from payload (without suppressing primary).
        for line in _emit_extra_parts(payload):
            yield line

        # Check for client cancellation after each event.
        if cancel_flag is not None and cancel_flag.get("cancelled", False):
            # Flush any open text/reasoning segments before abort.
            for line in _flush_text():
                yield line
            for line in _flush_reasoning():
                yield line
            step_active = False
            yield _make_part("abort")
            yield "data: [DONE]\n\n"
            return

    # ── Stream exhausted without terminal event (defensive) ───────────────
    # Flush any open text/reasoning segments.
    for line in _flush_text():
        yield line
    for line in _flush_reasoning():
        yield line
    if step_active:
        yield _make_part("finish-step")
        step_active = False
    yield _make_part("finish")
    yield "data: [DONE]\n\n"


__all__ = ["project_sse"]
