"""Transport-neutral stream_turn seam.

Extracts ``stream_turn()`` from the WS-coupled ``stream_agent_turn()`` in
``api/routers/ws/stream_events.py`` into a standalone transport-neutral module.
Both the WebSocket and SSE transports use ``stream_turn()`` to produce a stream
of ``RuntimeEvent`` objects from a ``ChatExecutionContext`` and a user message.

``stream_turn()`` calls the explicitly supplied
``AgentRuntime.aiter_chat_turn_stream()`` with a ``cancel_check`` that reads
``ctx.cancel_flag``. It threads only the supported legacy runtime controls as
kwargs and calls ``agent_runtime.set_execution_mode()`` when
``controls.execution_mode`` is not ``None``.

No import-time side effects.  No ``WebSocket``/``Request`` imports.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from fleet_rlm.api.config import AppConfig
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)


def _build_stream_kwargs(
    ctx: ChatExecutionContext,
    message: str,
) -> dict[str, Any]:
    """Build kwargs dict for ``AgentRuntime.aiter_chat_turn_stream``.

    Uses the legacy AgentRuntime allowlist only. Context-only controls such as
    ``trace_mode`` and ``selected_skill_ids`` stay on ``TurnControls`` for
    transports/future backends but are not accepted by the legacy runtime.
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

    return kwargs


async def _restore_session(
    ctx: ChatExecutionContext,
    agent_runtime: Any,
) -> None:
    """Restore agent session state when ``ctx.session_id`` is not ``None``.

    Looks up the persisted session record from ``ctx.prepared.persistence``
    (or ``ctx.prepared.repository``) and calls ``agent.aimport_session_state()``
    with the restored state. Does nothing if ``ctx.session_id`` is ``None``
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

    if restored_state and hasattr(agent_runtime, "aimport_session_state"):
        await agent_runtime.aimport_session_state(restored_state)
    elif hasattr(agent_runtime, "areset"):
        await agent_runtime.areset(clear_sandbox_buffers=True)


def _resolve_backend(ctx: ChatExecutionContext) -> ExecutionBackend:
    """Resolve which execution backend to dispatch to for this turn.

    Priority:
    1. ``ctx.controls.execution_backend`` (per-request override) if not ``None``
    2. ``AppConfig.execution_backend`` (process default, defaults to
       ``ExecutionBackend.legacy_agent_runtime``)
    """
    if ctx.controls.execution_backend is not None:
        return ctx.controls.execution_backend
    return AppConfig().execution_backend


async def stream_turn(
    *,
    ctx: ChatExecutionContext,
    agent_runtime: Any,
    message: str,
) -> AsyncIterator[RuntimeEvent]:
    """Stream one chat turn through the agent, yielding ``RuntimeEvent`` objects.

    This is the transport-neutral seam.  Both the WebSocket and SSE transports
    build a ``ChatExecutionContext`` and pass the context-managed
    AgentRuntime-like object explicitly — they do **not** call
    ``AgentRuntime.aiter_chat_turn_stream`` directly.

    The execution backend is resolved once at the top of the function:
    ``ctx.controls.execution_backend`` if not ``None``, else
    ``AppConfig.execution_backend``.  Currently supported backends:

    * ``ExecutionBackend.legacy_agent_runtime`` — the Phase 1
      ``AgentRuntime.aiter_chat_turn_stream`` path, unchanged.
    * ``ExecutionBackend.direct_rlm`` — dispatches to ``DirectRLMRunner``,
      which runs one ``RLMTurnSignature`` turn through the acquired Daytona
      interpreter when opted in via config or ``TurnControls``.

    Args:
        ctx: Transport-neutral context (prepared runtime, identity, session
            ids, cancel flag, per-turn controls).
        agent_runtime: AgentRuntime-like object for the legacy backend. This
            must not be a DSPy ``LM``.
        message: The user's message to process.

    Yields:
        ``RuntimeEvent`` objects from the underlying agent runtime.

    Raises:
        TypeError: When the legacy backend receives a non-AgentRuntime object.
        ValueError: When an unrecognised backend value is encountered.
        StopAsyncIteration: When the turn stream completes.
    """
    # Resolve which execution backend to use for this turn (stable once set).
    backend = _resolve_backend(ctx)

    if backend is ExecutionBackend.legacy_agent_runtime:
        # ── Phase 1 path: unchanged ──
        aiter_chat_turn_stream = getattr(agent_runtime, "aiter_chat_turn_stream", None)
        if not callable(aiter_chat_turn_stream):
            raise TypeError(
                f"legacy_agent_runtime backend expected AgentRuntime-like object, got {type(agent_runtime).__name__}"
            )

        # Apply execution mode if specified.
        if ctx.controls.execution_mode is not None:
            set_execution_mode = getattr(agent_runtime, "set_execution_mode", None)
            if not callable(set_execution_mode):
                raise TypeError(
                    "legacy_agent_runtime backend expected AgentRuntime-like object with set_execution_mode, "
                    f"got {type(agent_runtime).__name__}"
                )
            set_execution_mode(ctx.controls.execution_mode)

        # Restore session if a session_id was provided.
        await _restore_session(ctx, agent_runtime)

        # Build kwargs and delegate to the runtime.
        kwargs = _build_stream_kwargs(ctx, message)
        stream = aiter_chat_turn_stream(**kwargs)

        try:
            async for event in stream:
                yield event
        finally:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()

    elif backend is ExecutionBackend.direct_rlm:
        from fleet_rlm.rlm.runner import DirectRLMRunner

        runner = DirectRLMRunner()
        async for event in runner.stream(
            ctx=ctx,
            message=message,
            agent_runtime=agent_runtime,
            cancel_check=lambda: ctx.cancel_flag.get("cancelled", False),
        ):
            yield event

    else:
        raise ValueError(f"Unknown execution backend: {backend!r}")


__all__ = [
    "stream_turn",
    "_build_stream_kwargs",
    "_restore_session",
    "_resolve_backend",
]
