"""Transport-neutral stream_turn seam.

Extracts ``stream_turn()`` from the WS-coupled ``stream_agent_turn()`` in
``api/routers/ws/stream_events.py`` into a standalone transport-neutral module.
Both the WebSocket and SSE transports use ``stream_turn()`` to produce a stream
of ``RuntimeEvent`` objects from a ``ChatExecutionContext`` and a user message.

``stream_turn()`` calls ``AgentRuntime.aiter_chat_turn_stream()`` with a
``cancel_check`` that reads ``ctx.cancel_flag``.  It threads non-``None``
``TurnControls`` fields as kwargs and calls ``agent.set_execution_mode()``
when ``controls.execution_mode`` is not ``None``.

No import-time side effects.  No ``WebSocket``/``Request`` imports.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)


def _build_stream_kwargs(
    ctx: ChatExecutionContext,
    message: str,
) -> dict[str, Any]:
    """Build kwargs dict for ``AgentRuntime.aiter_chat_turn_stream``.

    Reads ``ctx.controls`` and threads non-``None`` fields as kwargs.
    The ``cancel_check`` lambda reads ``ctx.cancel_flag`` on each invocation.
    """
    kwargs: dict[str, Any] = {
        "message": message,
        "cancel_check": lambda: ctx.cancel_flag.get("cancelled", False),
    }

    controls = ctx.controls

    if controls.trace is not None:
        kwargs["trace"] = controls.trace
    if controls.docs_path is not None:
        kwargs["docs_path"] = controls.docs_path
    if controls.repo_url is not None:
        kwargs["repo_url"] = controls.repo_url
    if controls.repo_ref is not None:
        kwargs["repo_ref"] = controls.repo_ref
    if controls.context_paths:
        kwargs["context_paths"] = list(controls.context_paths)
    if controls.batch_concurrency is not None:
        kwargs["batch_concurrency"] = controls.batch_concurrency
    if controls.trace_mode is not None:
        kwargs["trace_mode"] = controls.trace_mode
    if controls.selected_skill_ids:
        kwargs["selected_skill_ids"] = list(controls.selected_skill_ids)

    return kwargs


async def _restore_session(
    ctx: ChatExecutionContext,
    agent: Any,
) -> None:
    """Restore agent session state when ``ctx.session_id`` is not ``None``.

    Looks up the persisted session record from ``ctx.prepared.persistence``
    (or ``ctx.prepared.repository``) and calls ``agent.aimport_session_state()``
    with the restored state.  Does nothing if ``ctx.session_id`` is ``None``
    or if no persistence store is available.
    """
    if ctx.session_id is None:
        return

    store = ctx.prepared.persistence or ctx.prepared.repository
    if store is None:
        return

    session_record = None
    get_record = getattr(store, "get_session_record", None)
    if callable(get_record):
        try:
            session_record = await get_record(session_id=ctx.session_id)
        except Exception:
            logger.debug(
                "Session record lookup failed for %s",
                ctx.session_id,
                exc_info=True,
            )

    if session_record is None:
        return

    # Extract state from the session record.
    session_data = session_record.get("session", {}) if isinstance(session_record, dict) else {}
    restored_state = session_data.get("state", {}) if isinstance(session_data, dict) else {}

    manifest_data = session_record.get("manifest", {}) if isinstance(session_record, dict) else {}
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})

    if restored_state and hasattr(agent, "aimport_session_state"):
        await agent.aimport_session_state(restored_state)
    elif hasattr(agent, "areset"):
        await agent.areset(clear_sandbox_buffers=True)


async def stream_turn(
    ctx: ChatExecutionContext,
    message: str,
) -> AsyncIterator[RuntimeEvent]:
    """Stream one chat turn through the agent, yielding ``RuntimeEvent`` objects.

    This is the transport-neutral seam.  Both the WebSocket and SSE transports
    build a ``ChatExecutionContext`` and call this function — they do **not**
    call ``AgentRuntime.aiter_chat_turn_stream`` directly.

    Args:
        ctx: Transport-neutral context (prepared runtime, identity, session
            ids, cancel flag, per-turn controls).
        message: The user's message to process.

    Yields:
        ``RuntimeEvent`` objects from the underlying agent runtime.

    Raises:
        StopAsyncIteration: When the turn stream completes.
    """
    # The transport layer is expected to replace planner_lm with an AgentRuntime
    # (or compatible object) that has set_execution_mode and aiter_chat_turn_stream.
    agent: Any = ctx.prepared.planner_lm

    # Apply execution mode if specified.
    if ctx.controls.execution_mode is not None:
        agent.set_execution_mode(ctx.controls.execution_mode)

    # Restore session if a session_id was provided.
    await _restore_session(ctx, agent)

    # Build kwargs and delegate to the runtime.
    kwargs = _build_stream_kwargs(ctx, message)
    stream = agent.aiter_chat_turn_stream(**kwargs)

    try:
        async for event in stream:
            yield event
    finally:
        aclose = getattr(stream, "aclose", None)
        if callable(aclose):
            await aclose()


__all__ = [
    "stream_turn",
    "_build_stream_kwargs",
    "_restore_session",
]
