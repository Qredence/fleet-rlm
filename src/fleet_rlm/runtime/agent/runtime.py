"""Agent runtime — owns interpreter, session state, tools, and execution context.

This module provides:

- ``AgentRuntime`` — simplified runtime using ``FleetAgent`` + ``discover_tools()``.
  This is the primary class for all new code.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, cast

import dspy
from dspy.streaming import StreamListener, StreamResponse

from fleet_rlm.integrations.daytona.async_compat import _run_async_compat
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventContext, RuntimeEventKind
from fleet_rlm.runtime.execution.streaming_events import (
    _normalize_trajectory,
)
from fleet_rlm.runtime.schemas import StreamEvent, StreamEventKind
from fleet_rlm.runtime.tools import discover_tools
from fleet_rlm.runtime.tools.binding import bind_runtime_tools, execute_sandbox_tool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _default_core_memory() -> dict[str, str]:
    return {
        "persona": (
            "I am a helpful AI assistant embedded in fleet-rlm, a recursive long-chain-of-thought "
            "agent system that delegates complex tasks to a fleet of specialised sub-agents via "
            "Daytona sandboxes. I am focused on helping developers understand and extend this system."
        ),
        "human": "The user is a developer working on the fleet-rlm project.",
        "scratchpad": "",
    }


def _append_turn_to_history(
    history: dspy.History,
    *,
    user_message: str,
    response: str,
    history_max_turns: int | None,
) -> dspy.History:
    messages = list(getattr(history, "messages", []) or [])
    messages.append({"user_message": user_message, "response": response})
    if history_max_turns is not None and len(messages) > history_max_turns:
        messages = messages[-history_max_turns:]
    return dspy.History(messages=messages)


def _prediction_value(result: Any, name: str) -> Any:
    if isinstance(result, dict) and name in result:
        return result.get(name)
    return getattr(result, name, None)


def _prediction_response_text(result: Any) -> str:
    for field_name in ("response", "assistant_response", "answer"):
        value = _prediction_value(result, field_name)
        if value not in (None, ""):
            return str(value)
    return ""


def _runtime_degradation_payload(result: Any) -> dict[str, Any]:
    runtime_degraded = bool(_prediction_value(result, "runtime_degraded") or _prediction_value(result, "degraded"))
    if not runtime_degraded:
        return {}

    payload: dict[str, Any] = {"runtime_degraded": True}
    for key in (
        "runtime_failure_category",
        "runtime_failure_phase",
        "runtime_fallback_used",
        "runtime_warning",
    ):
        value = _prediction_value(result, key)
        if value not in (None, ""):
            payload[key] = value

    payload.setdefault("runtime_failure_category", "rlm_fallback")
    payload.setdefault("runtime_failure_phase", "escalating_rlm")
    payload.setdefault("runtime_fallback_used", True)
    payload.setdefault("runtime_warning", "RLM escalation fell back to the lightweight response path.")
    return payload


def _runtime_routing_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    selected_skills = _prediction_value(result, "selected_skills")
    if isinstance(selected_skills, list):
        payload["selected_skills"] = [str(item) for item in selected_skills]
    routing_decision = _prediction_value(result, "routing_decision")
    if routing_decision not in (None, ""):
        payload["routing_decision"] = str(routing_decision)
    source_url = _prediction_value(result, "source_url")
    if source_url not in (None, ""):
        payload["source_url"] = str(source_url)
    return payload


def _routing_status_text(payload: dict[str, Any]) -> str:
    selected = ", ".join(payload.get("selected_skills", []))
    route = payload.get("routing_decision", "auto")
    source = payload.get("source_url")
    text = f"Route: {route}"
    if selected:
        text += f" | skills: {selected}"
    if source:
        text += f" | source: {source}"
    return text


def _get_streamable_react_program(program: Any) -> Any | None:
    # The program itself may be drivable (``FleetAgent``), or it may wrap a
    # streamable ReAct sub-program under ``.react`` (``EscalatingFleetModule``).
    # Probe the program first so a ``FleetAgent`` (whose ``.react`` is the
    # upstream planner ``Predict``, not a streamable program) is recognised.
    for candidate in (program, getattr(program, "react", None)):
        if candidate is None:
            continue
        planner = getattr(candidate, "planner", None)
        extract = getattr(candidate, "extract", None)
        async_call = getattr(candidate, "async_planner_step", None)
        if planner is not None and extract is not None and callable(async_call):
            return candidate
    return None


def _normalize_tool_args(tool_args: Any) -> dict[str, Any]:
    return dict(tool_args) if isinstance(tool_args, dict) else {}


def _observation_record(observation: Any) -> dict[str, Any]:
    if isinstance(observation, dict):
        return observation
    if not isinstance(observation, str):
        return {}
    stripped = observation.strip()
    if not stripped:
        return {}
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(stripped)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _recursive_child_review_payload(tool_name: str, observation: Any) -> dict[str, Any] | None:
    if tool_name not in {"delegate_to_rlm", "delegate_to_rlm_batched"}:
        return None

    record = _observation_record(observation)
    if not record:
        return None

    status = str(record.get("status", "")).lower()
    degraded = bool(record.get("degraded"))
    reviews = record.get("reviews")
    if status != "needs_human_review" and not degraded and not reviews:
        return None

    reason = (
        str(record.get("reason") or record.get("degradation_reason") or "recursive_child_degraded")
        .strip()
        .replace("_", " ")
    )
    return {
        "required": True,
        "reason": f"Recursive child result needs review: {reason}.",
        "repair_mode": "needs_human_review",
        "repair_target": "Review degraded recursive child output before accepting the run.",
        "repair_steps": ["Inspect the preserved child answer and degradation metadata."],
    }


async def _call_react_tool(tool: Any, tool_args: dict[str, Any]) -> Any:
    acall = getattr(tool, "acall", None)
    if callable(acall):
        return await acall(**tool_args)
    return await asyncio.to_thread(tool, **tool_args)


def _stream_event_from_runtime_event(event: RuntimeEvent) -> StreamEvent:
    return StreamEvent(
        kind=cast(StreamEventKind, event.kind.value),
        text=event.text,
        payload=dict(event.payload),
        timestamp=event.timestamp,
    )


def _build_tool_call_event(*, tool_name: str, tool_args: dict[str, Any], step_index: int) -> RuntimeEvent:
    return RuntimeEvent.tool_call(
        tool_name=tool_name,
        tool_args=tool_args,
        step_index=step_index,
    )


def _build_tool_result_event(*, tool_name: str, observation: Any, step_index: int) -> RuntimeEvent:
    return RuntimeEvent.tool_result(
        tool_name=tool_name,
        observation=observation,
        step_index=step_index,
    )


def _build_clarification_event(observation: Any) -> RuntimeEvent | None:
    if not isinstance(observation, dict) or observation.get("status") != "clarification_needed":
        return None

    import uuid as _uuid

    return RuntimeEvent.clarification(
        message_id=str(observation.get("message_id") or f"clar-{_uuid.uuid4().hex[:8]}"),
        question=observation.get("question"),
        step_label=observation.get("step_label", "Clarification needed"),
        options=observation.get("options", []),
    )


class AgentRuntime:
    """Simplified agent runtime managing FleetAgent, interpreter, history, tools, and core memory.

    This is the primary runtime class for new code.  It composes:

    - ``agent``: a :class:`~fleet_rlm.runtime.agent.agent.FleetAgent` instance
      initialised with tools discovered via :func:`~fleet_rlm.runtime.tools.discover_tools`.
    - ``interpreter``: Daytona interpreter for sandbox execution (optional).
    - ``history``: :class:`dspy.History` accumulating conversation turns.
    - ``tools``: list of tool callables registered with the agent.
    - ``core_memory``: key-value dict of persistent memory accessible by tools.
    """

    def __init__(
        self,
        *,
        interpreter: Any | None = None,
        max_iters: int = 10,
        rlm_max_iterations: int | None = None,
        rlm_max_llm_calls: int | None = None,
        rlm_max_output_chars: int | None = None,
        history_max_turns: int | None = 6,
        extra_tools: list[Any] | None = None,
        repository: Any | None = None,
        use_escalation: bool = True,
        summary_interval: int = 10,
        compaction_threshold_pct: float = 0.7,
    ) -> None:
        self.interpreter: Any | None = interpreter
        self.history: dspy.History = dspy.History(messages=[])
        self.history_max_turns: int | None = history_max_turns
        self.core_memory: dict[str, str] = self.default_core_memory()

        # Phase 7: attach runtime reference to interpreter so recursive children
        # can access parent history for bounded conversation snapshots
        if interpreter is not None:
            setattr(interpreter, "agent_runtime", self)

        # Session-management hooks used by the websocket layer
        self._db_session_id: str | object | None = None
        self._repository: Any | None = repository
        self._identity_rows: Any | None = None

        # Execution and document state
        self.execution_mode: str = "auto"
        self.loaded_document_paths: list[str] = []
        self.batch_concurrency: int | None = None
        self.rlm_max_iterations = rlm_max_iterations if rlm_max_iterations is not None else max_iters
        self.rlm_max_llm_calls = rlm_max_llm_calls if rlm_max_llm_calls is not None else 50
        self.rlm_max_output_chars = rlm_max_output_chars

        # Conversation summary for context compression (Phase 2)
        self.conversation_summary: str = ""
        self._summary_interval: int = summary_interval
        self._turns_since_summary: int = 0
        self._use_escalation: bool = use_escalation

        # Phase 7: token-budget-aware compaction threshold (keep history_max_turns as ceiling)
        self._compaction_threshold_pct: float = max(0.0, min(1.0, compaction_threshold_pct))

        # Discover tools from the registry; append any extra tools
        base_tools = discover_tools()
        base_tools = bind_runtime_tools(
            base_tools,
            runtime=self,
            interpreter=interpreter,
        )

        self._base_tools: list[Any] = base_tools + list(extra_tools or [])
        self._mcp_tools: list[Any] = []
        self.tools: list[Any] = list(self._base_tools)
        self.react_tools: list[Any] = self.tools

        # Retained for rebuilding the agent when async tool sources (e.g. MCP)
        # are attached after construction.
        self._max_iters: int = max_iters
        self._mcp_provider: Any | None = None

        self.agent: Any = self._build_agent(self.tools)

    def _build_agent(self, tools: list[Any]) -> Any:
        """Construct the cognition module for the given tool set."""
        from .agent import FleetAgent

        if self._use_escalation:
            from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

            return EscalatingFleetModule(
                interpreter=self.interpreter,
                tools=tools,
                max_iterations=self.rlm_max_iterations,
                max_llm_calls=self.rlm_max_llm_calls,
                max_output_chars=self.rlm_max_output_chars,
                summary_interval=self._summary_interval,
            )
        return FleetAgent(
            tools=tools,
            max_iters=self._max_iters,
        )

    async def attach_mcp_tools(self, configs: Any | None = None) -> list[str]:
        """Discover MCP tools and rebuild the agent with them registered.

        Connects to the configured MCP servers (env-driven by default), appends
        the discovered async ``dspy.Tool`` objects to the runtime tool list, and
        rebuilds the cognition module so they appear in the ReAct tool set. Safe
        to call when no MCP servers are configured (returns an empty list and
        leaves the agent untouched). Call :meth:`aclose_mcp` to release sessions.

        Returns the names of the MCP tools that were attached.
        """
        from fleet_rlm.runtime.tools.mcp_tools import MCPToolProvider, load_mcp_server_configs

        resolved = configs if configs is not None else load_mcp_server_configs()
        if not resolved:
            return []

        provider = MCPToolProvider(resolved)
        mcp_tools = await provider.connect()
        if not mcp_tools:
            await provider.aclose()
            return []

        # Release any previously attached provider before swapping it in.
        await self.aclose_mcp()
        self._mcp_provider = provider
        self._mcp_tools = list(mcp_tools)

        self.tools = list(self._base_tools) + list(self._mcp_tools)
        self.react_tools = self.tools
        self.agent = self._build_agent(self.tools)
        return [getattr(tool, "name", str(tool)) for tool in mcp_tools]

    async def aclose_mcp(self) -> None:
        """Close any live MCP sessions attached via :meth:`attach_mcp_tools`."""
        provider = self._mcp_provider
        if provider is None:
            return
        self._mcp_provider = None
        self._mcp_tools = []
        await provider.aclose()
        self.tools = list(self._base_tools)
        self.react_tools = self.tools
        self.agent = self._build_agent(self.tools)

    # -----------------------------------------------------------------
    # Chat API
    # -----------------------------------------------------------------

    def _estimate_history_chars(self) -> int:
        """Estimate character count of history as a proxy for token usage."""
        messages = list(getattr(self.history, "messages", []) or [])
        return sum(len(str(msg.get("user_message", ""))) + len(str(msg.get("response", ""))) for msg in messages)

    def _maybe_refresh_summary(self) -> None:
        """Regenerate conversation_summary based on token budget (Phase 7).

        Phase 7: Compacts history when estimated token usage crosses the
        compaction threshold, while keeping history_max_turns as a hard ceiling.
        """
        if not self._use_escalation:
            return

        self._turns_since_summary += 1

        # Phase 7: token-budget-aware compaction
        # Estimate history size and check against threshold
        history_chars = self._estimate_history_chars()
        # Use a reasonable default max context window if not available (64K tokens)
        # Approximate 4 chars per token for estimation
        max_context_chars = 64000 * 4
        threshold_chars = int(max_context_chars * self._compaction_threshold_pct)

        # Compact if threshold exceeded or interval reached
        should_compact = history_chars > threshold_chars or self._turns_since_summary >= self._summary_interval

        if should_compact:
            escalating = self.agent
            if hasattr(escalating, "compress_history"):
                try:
                    self.conversation_summary = escalating.compress_history(self.history)
                    self._turns_since_summary = 0
                    logger.debug(
                        "AgentRuntime: conversation summary refreshed (chars=%d, threshold=%d)",
                        history_chars,
                        threshold_chars,
                    )
                except Exception as exc:
                    logger.warning("AgentRuntime: summary refresh failed: %s", exc)

    def _escalation_call_args(self, user_message: str) -> dict[str, Any]:
        """Build call args for EscalatingFleetModule or FleetAgent."""
        if not self._use_escalation:
            return {"chat_history": self.history, "user_message": user_message}
        core_memory_str = "\n".join(f"[{k.upper()}]\n{v}" for k, v in self.core_memory.items() if v)
        return {
            "user_request": user_message,
            "core_memory": core_memory_str,
            "history": self.history,
            "execution_mode": self.execution_mode,
            "conversation_summary": self.conversation_summary,
        }

    def preview_routing(self, *, user_request: str, execution_mode: str = "auto") -> dict[str, Any]:
        """Expose deterministic route metadata before expensive turn execution."""
        preview_routing = getattr(self.agent, "preview_routing", None)
        if not callable(preview_routing):
            return {}
        payload = preview_routing(user_request=user_request, execution_mode=execution_mode)
        return payload if isinstance(payload, dict) else {}

    def _recursion_depth_state(self) -> tuple[int, int]:
        """Return ``(depth, max_depth)`` for the current runtime/interpreter.

        The root runtime is depth ``0``; ``max_depth`` is the configured
        ``sub_rlm`` recursion ceiling carried on the interpreter (default 2).
        """
        depth = int(getattr(self.interpreter, "_sub_rlm_depth", 0) or 0)
        max_depth = int(getattr(self.interpreter, "_sub_rlm_max_depth", 2) or 2)
        return depth, max_depth

    def _runtime_event_context(self) -> RuntimeEventContext:
        """Build the canonical runtime context (incl. recursion depth) for events.

        Phase 7: surfaces ``depth``/``max_depth`` so the frontend run-workbench
        can render recursion depth from typed ``RuntimeEvent.context`` fields.
        """
        depth, max_depth = self._recursion_depth_state()
        return RuntimeEventContext(
            execution_mode=self.execution_mode,
            depth=depth,
            max_depth=max_depth,
        )

    def _runtime_observability_payload(self) -> dict[str, Any]:
        """Return runtime metadata shared by streamed completion events."""
        depth, max_depth = self._recursion_depth_state()
        return {
            "execution_mode": self.execution_mode,
            "runtime_module": type(self.agent).__name__,
            "escalation_enabled": self._use_escalation,
            "conversation_summary_available": bool(self.conversation_summary),
            "loaded_document_count": len(self.loaded_document_paths),
            "recursion": {"depth": depth, "max_depth": max_depth},
            "rlm_limits": {
                "max_iterations": self.rlm_max_iterations,
                "max_llm_calls": self.rlm_max_llm_calls,
                "max_output_chars": self.rlm_max_output_chars,
            },
        }

    def chat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one synchronous chat turn and accumulate history.

        Args:
            user_message: The current user message.

        Returns:
            A :class:`dspy.Prediction` with at least a ``response`` field.
        """
        result = self.agent(**self._escalation_call_args(user_message))
        response = _prediction_response_text(result)
        self.history = _append_turn_to_history(
            self.history,
            user_message=user_message,
            response=response,
            history_max_turns=self.history_max_turns,
        )
        self._maybe_refresh_summary()
        return result

    async def achat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one chat turn from async callers without blocking the event loop."""
        return await asyncio.to_thread(self.chat_turn, user_message)

    async def _aiter_chat_turn_stream_posthoc(
        self,
        *,
        message: str,
        cancel_check: Callable[[], bool] | None,
    ) -> AsyncIterator[RuntimeEvent]:
        """Fallback stream path that emits events after the turn finishes."""
        if cancel_check is not None and cancel_check():
            yield RuntimeEvent(
                kind=RuntimeEventKind.DONE,
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        yield RuntimeEvent.status("Starting turn...")
        preview_routing = getattr(self.agent, "preview_routing", None)
        if callable(preview_routing):
            routing_preview = preview_routing(
                user_request=message,
                execution_mode=self.execution_mode,
            )
            if isinstance(routing_preview, dict) and routing_preview.get("routing_decision"):
                yield RuntimeEvent.status(
                    _routing_status_text(routing_preview),
                    payload=routing_preview,
                )

        try:
            async_call = getattr(self.agent, "aforward", None)
            if callable(async_call):
                result = await async_call(**self._escalation_call_args(message))
            else:
                # Drive sync-only modules in a worker thread. The RLM heavy path
                # runs sandbox code through the interpreter's blocking execute();
                # dspy.RLM.aforward still calls that synchronously (only LM
                # predictor calls are awaited), so to_thread is the correct
                # non-blocking pattern for modules without an explicit async path.
                # See docs/agent-harness/architecture-invariants.md.
                result = await asyncio.to_thread(
                    self.agent,
                    **self._escalation_call_args(message),
                )
        except Exception as exc:
            yield RuntimeEvent(
                kind=RuntimeEventKind.ERROR,
                text=str(exc),
                payload={"history_turns": self.history_turns()},
            )
            return

        if cancel_check is not None and cancel_check():
            yield RuntimeEvent(
                kind=RuntimeEventKind.DONE,
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        response = _prediction_response_text(result)
        trajectory_raw = getattr(result, "trajectory", None) or {}
        trajectory = _normalize_trajectory(trajectory_raw)
        degradation_payload = _runtime_degradation_payload(result)
        routing_payload = _runtime_routing_payload(result)

        if routing_payload.get("selected_skills") or routing_payload.get("routing_decision"):
            yield RuntimeEvent.status(
                _routing_status_text(routing_payload),
                payload=routing_payload,
            )

        for step in trajectory:
            thought = step.get("thought")
            tool_name = step.get("tool_name")
            is_terminal = (tool_name == "finish") or (not tool_name)
            if thought and not is_terminal:
                yield RuntimeEvent.reasoning(str(thought))

            tool_name = step.get("tool_name")
            if tool_name:
                tool_args = step.get("tool_args") or step.get("input", "")
                traj_idx = step.get("index")
                tool_ev = RuntimeEvent.tool_call(
                    tool_name=tool_name,
                    tool_args=tool_args if isinstance(tool_args, dict) else {"input": tool_args},
                    step_index=traj_idx,
                )
                tool_ev.payload["step"] = step
                tool_ev.payload["trajectory_index"] = traj_idx
                yield tool_ev

            observation = step.get("observation") or step.get("output", "")
            if observation and tool_name:
                result_ev = RuntimeEvent.tool_result(
                    tool_name=tool_name,
                    observation=observation,
                    step_index=step.get("index"),
                )
                result_ev.payload["output"] = observation
                result_ev.payload["step"] = step
                result_ev.payload["trajectory_index"] = step.get("index")
                yield result_ev
                clar_ev = _build_clarification_event(observation)
                if clar_ev is not None:
                    yield clar_ev

        if degradation_payload:
            yield RuntimeEvent(
                kind=RuntimeEventKind.WARNING,
                text=str(degradation_payload["runtime_warning"]),
                payload=degradation_payload,
            )

        if response:
            yield RuntimeEvent(kind=RuntimeEventKind.TEXT, text=response)

        self.history = _append_turn_to_history(
            self.history,
            user_message=message,
            response=response,
            history_max_turns=self.history_max_turns,
        )
        self._maybe_refresh_summary()

        done_payload: dict[str, Any] = {
            "trajectory": {"steps": trajectory},
            "history_turns": self.history_turns(),
        }
        done_payload.update(self._runtime_observability_payload())
        done_payload.update(degradation_payload)
        done_payload.update(routing_payload)
        yield RuntimeEvent(
            kind=RuntimeEventKind.DONE,
            text=response,
            payload=done_payload,
            context=self._runtime_event_context(),
        )

    # -----------------------------------------------------------------
    # Async context manager (required by ChatAgentProtocol)
    # -----------------------------------------------------------------

    def __enter__(self) -> AgentRuntime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        _ = exc_type, exc_val, exc_tb
        self.shutdown()
        return False

    async def __aenter__(self) -> AgentRuntime:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        _ = exc_type, exc_val, exc_tb
        await self.ashutdown()
        return False

    def shutdown(self) -> None:
        _run_async_compat(self.aclose_mcp)
        if self.interpreter is not None:
            shutdown = getattr(self.interpreter, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:
                    pass
            else:
                ashutdown = getattr(self.interpreter, "ashutdown", None)
                if callable(ashutdown):
                    try:
                        _run_async_compat(ashutdown)
                    except Exception:
                        pass

    async def ashutdown(self) -> None:
        await self.aclose_mcp()
        if self.interpreter is None:
            return
        ashutdown = getattr(self.interpreter, "ashutdown", None)
        if callable(ashutdown):
            try:
                await ashutdown()
            except Exception:
                pass
            return
        shutdown = getattr(self.interpreter, "shutdown", None)
        if callable(shutdown):
            try:
                await asyncio.to_thread(shutdown)
            except Exception:
                pass

    # -----------------------------------------------------------------
    # ChatAgentProtocol surface
    # -----------------------------------------------------------------

    @staticmethod
    def default_core_memory() -> dict[str, str]:
        return _default_core_memory()

    def history_turns(self) -> int:
        return len(list(getattr(self.history, "messages", []) or []))

    def set_execution_mode(self, execution_mode: str) -> None:
        self.execution_mode = execution_mode

    def load_document(self, path: str, alias: str = "active") -> None:
        _ = alias
        self.loaded_document_paths.append(path)

    def export_session_state(self) -> dict[str, Any]:
        payload = self.export_session(session_id=str(self._db_session_id or "unknown"))
        interpreter_state = self._export_interpreter_session_state()
        if interpreter_state:
            payload.update(interpreter_state)
        return payload

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        summary = self.import_session(data=state)
        self._import_interpreter_session_state(state)
        return summary

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        summary = self.import_session(data=state)
        await self._aimport_interpreter_session_state(state)
        return summary

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        if clear_sandbox_buffers and self.interpreter is not None:
            execute_sandbox_tool(
                self.interpreter,
                "clear_buffer()\nSUBMIT(status='ok')",
                {},
            )
        self.history = dspy.History(messages=[])
        self.core_memory = self.default_core_memory()
        self.loaded_document_paths = []
        self.batch_concurrency = None
        self.conversation_summary = ""
        self._turns_since_summary = 0
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        return self.reset(clear_sandbox_buffers=clear_sandbox_buffers)

    def _export_interpreter_session_state(self) -> dict[str, Any]:
        if self.interpreter is None:
            return {}
        export_state = getattr(self.interpreter, "export_session_state", None)
        if not callable(export_state):
            return {}
        try:
            exported = export_state()
        except Exception:
            return {}
        return exported if isinstance(exported, dict) else {}

    def _import_interpreter_session_state(self, state: dict[str, Any]) -> None:
        if self.interpreter is None or "daytona" not in state:
            return
        import_state = getattr(self.interpreter, "import_session_state", None)
        if not callable(import_state):
            return
        import_state(state)

    async def _aimport_interpreter_session_state(self, state: dict[str, Any]) -> None:
        if self.interpreter is None or "daytona" not in state:
            return
        async_import_state = getattr(self.interpreter, "aimport_session_state", None)
        if callable(async_import_state):
            await async_import_state(state)
            return
        self._import_interpreter_session_state(state)

    async def execute_command(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        from .commands import execute_command as _execute_command

        return await _execute_command(self, command, args)

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream one chat turn through the agent, yielding events.

        This is the canonical entrypoint used by the websocket streaming
        layer.  It runs a single forward pass, extracts the trajectory,
        and yields ``StreamEvent`` objects for each step plus a terminal
        ``done`` event.
        """
        _ = trace
        _ = docs_path
        _ = repo_url
        _ = repo_ref
        _ = context_paths
        _ = volume_name
        if batch_concurrency is not None:
            self.batch_concurrency = batch_concurrency

        react_program = _get_streamable_react_program(self.agent)
        if react_program is None:
            logger.info("streaming_path=posthoc (react_program not streamable)")
            async for event in self._aiter_chat_turn_stream_posthoc(
                message=message,
                cancel_check=cancel_check,
            ):
                yield _stream_event_from_runtime_event(event)
            return
        logger.info("streaming_path=native (dspy.streamify per-token streaming)")

        if cancel_check is not None and cancel_check():
            yield StreamEvent(
                kind="done",
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        import time as _time

        t_turn_start = _time.monotonic()

        yield StreamEvent(kind="status", text="Starting turn...")

        input_args = {
            "chat_history": self.history,
            "user_message": message,
        }
        trajectory_raw: dict[str, Any] = {}
        extract_prediction: dspy.Prediction | None = None
        response_streamed = False
        final_reasoning = ""
        response = ""
        recursive_child_review: dict[str, Any] | None = None

        try:
            max_iters = int(getattr(react_program, "max_iters", 1) or 1)
            for step_index in range(max_iters):
                if cancel_check is not None and cancel_check():
                    yield StreamEvent(
                        kind="done",
                        text="[cancelled]",
                        payload={"cancelled": True, "history_turns": self.history_turns()},
                    )
                    return

                try:
                    t_planner_start = _time.monotonic()
                    prediction = await react_program.async_planner_step(
                        trajectory_raw,
                        **input_args,
                    )
                    t_planner_ms = (_time.monotonic() - t_planner_start) * 1000
                    logger.info("streaming: planner step %d completed in %.0fms", step_index, t_planner_ms)
                except ValueError:
                    break

                thought = str(getattr(prediction, "next_thought", "") or "")
                tool_name = str(getattr(prediction, "next_tool_name", "") or "")
                tool_args = _normalize_tool_args(getattr(prediction, "next_tool_args", {}))

                trajectory_raw[f"thought_{step_index}"] = thought
                trajectory_raw[f"tool_name_{step_index}"] = tool_name
                trajectory_raw[f"tool_args_{step_index}"] = tool_args

                is_terminal = (tool_name == "finish") or (not tool_name)

                if thought:
                    if is_terminal:
                        response = thought
                        response_streamed = True
                        yield StreamEvent(kind="text", text=thought)
                    else:
                        yield StreamEvent(
                            kind="reasoning",
                            text=thought,
                            payload={"phase": "reasoning", "step_index": step_index},
                        )
                elif is_terminal:
                    response = ""
                    response_streamed = True

                if not tool_name:
                    break

                if tool_name == "finish":
                    trajectory_raw[f"observation_{step_index}"] = "Completed."
                    break

                tool = react_program.tools[tool_name]
                yield _stream_event_from_runtime_event(
                    _build_tool_call_event(tool_name=tool_name, tool_args=tool_args, step_index=step_index)
                )

                try:
                    observation = await _call_react_tool(tool, tool_args)
                except Exception as err:
                    observation = f"Execution error in {tool_name}: {err}"

                trajectory_raw[f"observation_{step_index}"] = observation
                if recursive_child_review is None:
                    recursive_child_review = _recursive_child_review_payload(tool_name, observation)
                yield _stream_event_from_runtime_event(
                    _build_tool_result_event(
                        tool_name=tool_name,
                        observation=observation,
                        step_index=step_index,
                    )
                )

                clarification_event = _build_clarification_event(observation)
                if clarification_event is not None:
                    yield _stream_event_from_runtime_event(clarification_event)

            # Fast path: skip the extract LLM call when the agent finished
            # with a finish tool or no tool. The planner thought already
            # contains the final response, which we yielded directly as text.
            if response_streamed:
                t_fast_ms = (_time.monotonic() - t_turn_start) * 1000
                logger.info(
                    "streaming: terminal planner thought used as final response, skipping extract LLM call (%.0fms since turn start)",
                    t_fast_ms,
                )
                extract_prediction = dspy.Prediction(response=response)
            else:
                t_extract_start = _time.monotonic()
                stream_extract = cast(
                    Callable[..., AsyncIterator[Any]],
                    dspy.streamify(
                        react_program.extract.predict,
                        stream_listeners=[StreamListener(signature_field_name="response")],
                        include_final_prediction_in_output_stream=True,
                        async_streaming=True,
                    ),
                )
                async for chunk in stream_extract(
                    **input_args,
                    trajectory=react_program._format_trajectory(trajectory_raw),
                ):
                    if isinstance(chunk, StreamResponse):
                        if chunk.signature_field_name == "response" and chunk.chunk:
                            response_streamed = True
                            response += chunk.chunk
                            yield StreamEvent(kind="text", text=chunk.chunk)
                        continue

                    if isinstance(chunk, dspy.Prediction):
                        extract_prediction = chunk
                t_extract_ms = (_time.monotonic() - t_extract_start) * 1000
                logger.info("streaming: extract completed in %.0fms", t_extract_ms)
        except Exception as exc:
            yield StreamEvent(
                kind="error",
                text=str(exc),
                payload={"history_turns": self.history_turns()},
            )
            return

        if extract_prediction is None:
            yield StreamEvent(
                kind="error",
                text="Streaming turn ended without a final prediction.",
                payload={"history_turns": self.history_turns()},
            )
            return

        if cancel_check is not None and cancel_check():
            yield StreamEvent(
                kind="done",
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.history_turns()},
            )
            return

        if not response_streamed:
            response = str(getattr(extract_prediction, "response", "") or response)
        final_reasoning = str(getattr(extract_prediction, "reasoning", "") or "")
        trajectory = _normalize_trajectory(trajectory_raw)

        if response and not response_streamed:
            yield StreamEvent(kind="text", text=response)

        self.history = _append_turn_to_history(
            self.history,
            user_message=message,
            response=response,
            history_max_turns=self.history_max_turns,
        )
        self._maybe_refresh_summary()

        done_payload: dict[str, Any] = {
            "trajectory": {"steps": trajectory},
            "history_turns": self.history_turns(),
        }
        done_payload.update(self._runtime_observability_payload())
        if recursive_child_review is not None:
            done_payload.update(
                {
                    "human_review": recursive_child_review,
                    "runtime_degraded": True,
                    "runtime_failure_category": "recursive_child_degraded",
                    "runtime_failure_phase": "delegate_to_rlm",
                }
            )
        if final_reasoning:
            done_payload["final_reasoning"] = final_reasoning

        t_total_ms = (_time.monotonic() - t_turn_start) * 1000
        logger.info("streaming: turn completed in %.0fms", t_total_ms)
        yield StreamEvent(kind="done", text=response, payload=done_payload)

    # -----------------------------------------------------------------
    # Core memory API (accessible by tools)
    # -----------------------------------------------------------------

    def get_core_memory(self) -> dict[str, str]:
        """Return the core memory dict for tool access."""
        return self.core_memory

    def get_core_memory_key(self, key: str) -> str | None:
        """Read a single key from core memory.

        Args:
            key: Memory key.

        Returns:
            Associated value string, or ``None`` if absent.
        """
        return self.core_memory.get(key)

    def set_core_memory_key(self, key: str, value: str) -> None:
        """Write a key-value pair to core memory.

        Args:
            key: Memory key.
            value: Text value to store.
        """
        self.core_memory[key] = value

    # -----------------------------------------------------------------
    # Session persistence helpers
    # -----------------------------------------------------------------

    def export_session(self, session_id: str) -> dict[str, Any]:
        """Export the full session state as a JSON-compatible dict.

        Delegates to :func:`~fleet_rlm.runtime.agent.persistence.export_session`.

        Args:
            session_id: Session identifier to embed in the payload.

        Returns:
            JSON-compatible dict with ``schema_version``, ``session_id``,
            ``timestamp``, ``turns``, and ``core_memory``.
        """
        from .persistence import export_session as _export_session

        return _export_session(self, session_id)

    def import_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """Restore session state from a previously exported dict.

        Delegates to :func:`~fleet_rlm.runtime.agent.persistence.import_session`.

        Args:
            data: Dict previously produced by :meth:`export_session`.

        Returns:
            Summary dict with ``status``, ``session_id``, and
            ``history_turns``.
        """
        from .persistence import import_session as _import_session

        return _import_session(self, data)
