"""Agent runtime — owns interpreter, session state, tools, and execution context.

This module provides two runtime classes:

- ``AgentRuntime`` — simplified runtime using ``FleetAgent`` + ``discover_tools()``.
  This is the primary class for new code.
- ``_LegacyAgentRuntime`` — complex legacy runtime (to be deleted).
  Used by ``chat_agent.py`` for backward compatibility.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

import dspy

from fleet_rlm.runtime.execution.document_cache import DocumentCacheMixin
from fleet_rlm.runtime.execution.validation import ValidationConfig
from fleet_rlm.runtime.models.streaming import StreamEvent
from fleet_rlm.runtime.tools import ExecutionMode, build_tool_list, discover_tools

if TYPE_CHECKING:
    from .agent import FleetAgent

from .agent import RLMReActAgent
from .chat_session_state import (
    append_history as _append_history,
    export_session_state as _export_session_state,
    history_messages as _history_messages,
    history_turns as _history_turns,
    import_session_state as _import_session_state,
    aimport_session_state as _aimport_session_state,
)
from .memory import CoreMemoryMixin
from .signatures import RLMReActChatSignature

_DEFAULT_HISTORY_MAX_TURNS = 6


class _LegacyAgentRuntime(DocumentCacheMixin, CoreMemoryMixin):
    """Mutable runtime state for an RLM ReAct chat session (legacy).

    Owns the interpreter, conversation history, tool registry, core memory,
    document cache, and session persistence hooks.  This separation keeps the
    DSPy ``dspy.Module`` thin and free of side-effectful lifecycle code.

    .. deprecated::
        Use ``AgentRuntime`` (simplified) for new code.
    """

    def __init__(
        self,
        *,
        react_max_iters: int = 10,
        deep_react_max_iters: int = 35,
        enable_adaptive_iters: bool = True,
        rlm_max_iterations: int = 30,
        rlm_max_llm_calls: int = 50,
        timeout: int = 900,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
        runtime: Any | None = None,
        verbose: bool = False,
        history_max_turns: int | None = _DEFAULT_HISTORY_MAX_TURNS,
        extra_tools: list[Callable[..., Any]] | None = None,
        interpreter: Any | None = None,
        max_depth: int = 2,
        current_depth: int = 0,
        interpreter_async_execute: bool = True,
        delete_session_on_shutdown: bool = True,
        sandbox_spec: Any | None = None,
        sub_lm: Any | None = None,
        guardrail_mode: Literal["off", "warn", "strict"] = "warn",
        max_output_chars: int = 10000,
        min_substantive_chars: int = 20,
        delegate_lm: Any | None = None,
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
        recursive_reflection_enabled: bool = False,
        recursive_context_selection_enabled: bool = False,
        execution_mode: ExecutionMode = "auto",
    ) -> None:
        self._init_document_cache()
        self._init_core_memory()

        self.react_max_iters = react_max_iters
        self.deep_react_max_iters = max(react_max_iters, deep_react_max_iters)
        self.enable_adaptive_iters = enable_adaptive_iters
        self.rlm_max_iterations = rlm_max_iterations
        self.rlm_max_llm_calls = rlm_max_llm_calls
        self.verbose = verbose
        self.history_max_turns = history_max_turns
        self._max_depth = max_depth
        self._current_depth = current_depth
        self.delegate_lm = delegate_lm
        self.delegate_max_calls_per_turn = max(1, int(delegate_max_calls_per_turn))
        self.delegate_result_truncation_chars = max(
            256, int(delegate_result_truncation_chars)
        )
        self.recursive_reflection_enabled = bool(recursive_reflection_enabled)
        self.recursive_context_selection_enabled = bool(
            recursive_context_selection_enabled
        )
        self.execution_mode: ExecutionMode = execution_mode
        self.secret_name = secret_name
        self.default_volume_name = volume_name
        self.loaded_document_paths: list[str] = []
        self.batch_concurrency: int | None = None
        self._last_tool_error_count = 0
        from .turn_state import TurnDelegationState

        self._turn_delegation_state = TurnDelegationState(
            effective_max_iters=react_max_iters
        )
        self._live_event_callback: Callable[[StreamEvent], Any] | None = None

        self._validation_config = ValidationConfig(
            guardrail_mode=guardrail_mode,
            max_output_chars=max_output_chars,
            min_substantive_chars=min_substantive_chars,
        )

        if interpreter is not None:
            self.interpreter = interpreter
        else:
            from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

            self.interpreter = DaytonaInterpreter(
                runtime=runtime,
                timeout=timeout,
                volume_name=volume_name,
                delete_session_on_shutdown=delete_session_on_shutdown,
                max_llm_calls=rlm_max_llm_calls,
                async_execute=interpreter_async_execute,
                sandbox_spec=sandbox_spec,
                sub_lm=sub_lm,
            )

        self.history = dspy.History(messages=[])

        self._started = False
        self._extra_tools: list[Callable[..., Any]] = list(extra_tools or [])
        self._runtime_modules: dict[str, dspy.Module] = {}
        self._recursive_reflection_module: dspy.Module | None = None
        self._recursive_context_selection_module: dspy.Module | None = None

        # Optional database linkage (set by transport layer)
        self._repository: Any | None = None
        self._identity_rows: Any | None = None
        self._db_session_id: str | uuid.UUID | None = None

        # Register Core Memory tools
        self._extra_tools.extend([self.core_memory_append, self.core_memory_replace])

        self.react_tools: list[Callable[..., Any]] = []
        self.agent = self._build_agent()

    @property
    def current_depth(self) -> int:
        return self._current_depth

    @property
    def react(self) -> dspy.ReAct:
        return self.agent.react

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self.interpreter.start()
        self._started = True

    def shutdown(self) -> None:
        self.interpreter.shutdown()
        self._started = False

    async def astart(self) -> None:
        if self._started:
            return
        if getattr(self.interpreter, "async_execute", False) and hasattr(
            self.interpreter, "astart"
        ):
            await self.interpreter.astart()
        else:
            self.interpreter.start()
        self._started = True

    async def ashutdown(self) -> None:
        if getattr(self.interpreter, "async_execute", False) and hasattr(
            self.interpreter, "ashutdown"
        ):
            await self.interpreter.ashutdown()
        else:
            self.interpreter.shutdown()
        self._started = False

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Reset chat history, document cache, and (optionally) sandbox buffers."""
        docs_count = self.clear_document_cache()
        self.history = dspy.History(messages=[])
        return {
            "status": "ok",
            "history_turns": 0,
            "documents_cleared": docs_count,
            "buffers_cleared": clear_sandbox_buffers,
        }

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Async reset variant that can safely await async sandbox tools."""
        docs_count = self.clear_document_cache()
        self.history = dspy.History(messages=[])
        if clear_sandbox_buffers and self.interpreter is not None:
            try:
                code = "clear_buffer(name='all')"
                if getattr(self.interpreter, "async_execute", False) and hasattr(
                    self.interpreter, "aexecute"
                ):
                    await self.interpreter.aexecute(code)
                elif hasattr(self.interpreter, "execute"):
                    self.interpreter.execute(code)
            except Exception:
                pass  # best-effort; do not let buffer clearing block the reset
        return {
            "status": "ok",
            "history_turns": 0,
            "documents_cleared": docs_count,
            "buffers_cleared": clear_sandbox_buffers,
        }

    # -----------------------------------------------------------------
    # Session state
    # -----------------------------------------------------------------

    def export_session_state(self) -> dict[str, Any]:
        return _export_session_state(self)

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return _import_session_state(self, state)

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return await _aimport_session_state(self, state)

    # -----------------------------------------------------------------
    # History helpers
    # -----------------------------------------------------------------

    def history_messages(self) -> list[Any]:
        return _history_messages(self)

    def history_turns(self) -> int:
        return _history_turns(self)

    def append_history(self, user_request: str, assistant_response: str) -> None:
        _append_history(self, user_request, assistant_response)

    # -----------------------------------------------------------------
    # Tool management
    # -----------------------------------------------------------------

    def register_extra_tool(self, tool: Callable[..., Any]) -> dict[str, Any]:
        self._extra_tools.append(tool)
        self.agent = self._build_agent()
        return {"status": "ok", "tool_name": getattr(tool, "__name__", str(tool))}

    def set_execution_mode(self, execution_mode: ExecutionMode) -> None:
        normalized: ExecutionMode = (
            execution_mode
            if execution_mode in {"auto", "rlm_only", "tools_only"}
            else "auto"
        )
        if self.execution_mode == normalized:
            return
        self.execution_mode = normalized
        self.agent = self._build_agent()

    # -----------------------------------------------------------------
    # Runtime modules
    # -----------------------------------------------------------------

    def get_runtime_module(self, name: str) -> dspy.Module:
        from fleet_rlm.runtime.models.builders import build_runtime_module_config
        from fleet_rlm.runtime.models.registry import get_or_build_runtime_module

        return get_or_build_runtime_module(
            self._runtime_modules,
            name,
            config=build_runtime_module_config(
                interpreter=self.interpreter,
                max_iterations=self.rlm_max_iterations,
                max_llm_calls=self.rlm_max_llm_calls,
                verbose=self.verbose,
            ),
        )

    def get_recursive_reflection_module(self) -> dspy.Module:
        if self._recursive_reflection_module is None:
            from .recursive_reflection import ReflectAndReviseWorkspaceStepModule

            self._recursive_reflection_module = ReflectAndReviseWorkspaceStepModule()
        return self._recursive_reflection_module

    def get_recursive_context_selection_module(self) -> dspy.Module:
        if self._recursive_context_selection_module is None:
            from .recursive_context_selection import (
                AssembleRecursiveWorkspaceContextModule,
            )

            self._recursive_context_selection_module = (
                AssembleRecursiveWorkspaceContextModule()
            )
        return self._recursive_context_selection_module

    # -----------------------------------------------------------------
    # Workspace / streaming helpers (moved from facade)
    # -----------------------------------------------------------------

    def _validate_assistant_response(
        self,
        *,
        assistant_response: str,
        trajectory: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        from fleet_rlm.runtime.execution.validation import validate_assistant_response

        return validate_assistant_response(
            assistant_response=assistant_response,
            trajectory=trajectory,
            config=self._validation_config,
        )

    def _effective_context_paths(
        self, *, docs_path: str | None, context_paths: list[str] | None
    ) -> list[str]:
        from fleet_rlm.utils.paths import dedupe_paths, is_local_path

        docs_paths = [str(docs_path)] if docs_path is not None else []
        candidates = [
            *self.loaded_document_paths,
            *(context_paths or []),
            *docs_paths,
        ]
        return dedupe_paths([p for p in candidates if is_local_path(p)])

    def prepare_routed_turn(self, *, effective_max_iters: int | None = None) -> int:
        from .chat_turns import prepare_routed_turn

        return prepare_routed_turn(
            self,
            effective_max_iters=effective_max_iters,
        )

    async def _aconfigure_workspace(
        self,
        *,
        docs_path: str | None,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> None:
        interpreter = getattr(self, "interpreter", None)
        configure_workspace = getattr(interpreter, "aconfigure_workspace", None)
        if not callable(configure_workspace):
            return
        await configure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=self._effective_context_paths(
                docs_path=docs_path,
                context_paths=context_paths,
            ),
            volume_name=volume_name,
        )

    async def _aensure_workspace_session(self) -> None:
        interpreter = getattr(self, "interpreter", None)
        if interpreter is None:
            return
        if (
            getattr(interpreter, "_session", None) is None
            and getattr(interpreter, "_persisted_sandbox_id", None) is None
        ):
            return
        get_session = getattr(interpreter, "aget_session", None)
        if callable(get_session):
            await get_session()

    def _bootstrap_status_event(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
    ) -> StreamEvent:
        interpreter = getattr(self, "interpreter", None)
        runtime_payload = {
            "runtime_mode": "daytona_pilot",
            "execution_mode": self.execution_mode,
            "depth": self.current_depth,
            "max_depth": self._max_depth,
            "execution_profile": str(
                getattr(
                    getattr(interpreter, "default_execution_profile", None),
                    "value",
                    getattr(interpreter, "default_execution_profile", None),
                )
            ),
            "sandbox_active": False,
            "sandbox_id": None,
            "effective_max_iters": max(self.react_max_iters, self.rlm_max_iterations),
            "volume_name": volume_name,
        }
        return StreamEvent(
            kind="status",
            text="Bootstrapping Daytona RLM session",
            payload={
                "runtime_mode": "daytona_pilot",
                "repo_url": repo_url,
                "repo_ref": repo_ref,
                "context_paths": context_paths,
                "runtime": runtime_payload,
            },
        )

    def _enrich_runtime_event_payload(
        self,
        event: StreamEvent,
        *,
        volume_name: str | None,
    ) -> StreamEvent:
        payload = dict(event.payload or {})
        runtime_payload = dict(payload.get("runtime", {}) or {})
        runtime_payload.setdefault("runtime_mode", "daytona_pilot")
        runtime_payload.setdefault(
            "volume_name",
            volume_name
            if volume_name is not None
            else getattr(self.interpreter, "volume_name", None),
        )
        payload["runtime"] = runtime_payload
        payload.setdefault("runtime_mode", "daytona_pilot")
        return StreamEvent(
            kind=event.kind,
            text=event.text,
            payload=payload,
            timestamp=event.timestamp,
            flush_tokens=event.flush_tokens,
        )

    # -----------------------------------------------------------------
    # Chat turn (used by streaming fallback)
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Run one synchronous chat turn and return the result payload."""
        from .chat_turns import (
            prepare_turn,
            process_prediction_to_turn_result,
            snapshot_turn_metrics,
            turn_metrics_from_prediction,
        )

        self.start()
        if self.execution_mode == "rlm_only":
            from .forced_routing import run_forced_rlm_turn

            prediction = run_forced_rlm_turn(self, message=message)
            return process_prediction_to_turn_result(
                self,
                prediction=prediction,
                message=message,
                include_core_memory_snapshot=False,
                turn_metrics=snapshot_turn_metrics(self),
            )

        effective_max_iters = prepare_turn(self, message)
        prediction = self.agent(
            user_request=message,
            history=self.history,
            core_memory=self.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        return process_prediction_to_turn_result(
            self,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=True,
            finalize_and_validate=True,
            turn_metrics=turn_metrics_from_prediction(
                prediction,
                snapshot_turn_metrics(self),
            ),
        )

    async def achat_turn(self, message: str) -> dict[str, Any]:
        """Run one asynchronous chat turn and return the result payload."""
        from .chat_turns import (
            prepare_turn,
            process_prediction_to_turn_result,
            snapshot_turn_metrics,
            turn_metrics_from_prediction,
        )

        self.start()
        if self.execution_mode == "rlm_only":
            from .forced_routing import arun_forced_rlm_turn

            prediction = await arun_forced_rlm_turn(self, message=message)
            return process_prediction_to_turn_result(
                self,
                prediction=prediction,
                message=message,
                include_core_memory_snapshot=False,
                turn_metrics=snapshot_turn_metrics(self),
            )

        effective_max_iters = prepare_turn(self, message)
        prediction = await self.agent.react.acall(
            user_request=message,
            history=self.history,
            core_memory=self.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        return process_prediction_to_turn_result(
            self,
            prediction=prediction,
            message=message,
            include_core_memory_snapshot=True,
            finalize_and_validate=True,
            turn_metrics=turn_metrics_from_prediction(
                prediction,
                snapshot_turn_metrics(self),
            ),
        )

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _build_agent(self) -> RLMReActAgent:
        self.react_tools = build_tool_list(self, self._extra_tools)
        return RLMReActAgent(
            signature=RLMReActChatSignature,
            tools=list(self.react_tools),
            max_iters=self.react_max_iters,
        )


# ---------------------------------------------------------------------------
# Simplified AgentRuntime (new primary class)
# ---------------------------------------------------------------------------


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
        history_max_turns: int | None = 6,
        extra_tools: list[Any] | None = None,
    ) -> None:
        from .agent import FleetAgent

        self.interpreter: Any | None = interpreter
        self.history: dspy.History = dspy.History(messages=[])
        self.history_max_turns: int | None = history_max_turns
        self.core_memory: dict[str, str] = {
            "persona": "I am a helpful AI assistant focused on writing high-quality code.",
            "human": "The user is a developer working on this project.",
            "scratchpad": "",
        }

        # Discover tools from the registry; append any extra tools
        base_tools = discover_tools()
        self.tools: list[Any] = base_tools + list(extra_tools or [])

        # Initialise agent with the discovered tool set
        self.agent: FleetAgent = FleetAgent(
            tools=self.tools,
            max_iters=max_iters,
        )

    # -----------------------------------------------------------------
    # Chat API
    # -----------------------------------------------------------------

    def chat_turn(self, user_message: str) -> dspy.Prediction:
        """Run one synchronous chat turn and accumulate history.

        Args:
            user_message: The current user message.

        Returns:
            A :class:`dspy.Prediction` with at least a ``response`` field.
        """
        result = self.agent.forward(
            chat_history=self.history,
            user_message=user_message,
        )
        response = str(getattr(result, "response", ""))
        messages = list(getattr(self.history, "messages", []) or [])
        messages.append({"user_message": user_message, "response": response})
        if (
            self.history_max_turns is not None
            and len(messages) > self.history_max_turns
        ):
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)
        return result

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
