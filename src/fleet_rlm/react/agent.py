"""Interactive DSPy ReAct chat agent backed by ModalInterpreter + RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
existing ``ModalInterpreter`` and ``dspy.RLM`` workflows in this project.

Tool implementations live in :mod:`fleet_rlm.react_tools`, streaming logic
in :mod:`fleet_rlm.streaming`, and command dispatch in
:mod:`fleet_rlm.commands`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Iterable

import dspy

from .commands import execute_command as _execute_command
from ..interactive.models import StreamEvent
from ..core.interpreter import ModalInterpreter
from .tools import build_tool_list
from .streaming import aiter_chat_turn_stream as _aiter_stream
from .streaming import iter_chat_turn_stream as _iter_stream


class RLMReActChatSignature(dspy.Signature):
    """Interactive ReAct chat signature with explicit conversation history."""

    user_request: str = dspy.InputField(desc="Current user request in the chat session")
    history: dspy.History = dspy.InputField(
        desc="Prior chat turns using keys user_request and assistant_response"
    )
    assistant_response: str = dspy.OutputField(desc="Final assistant response to user")


class RLMReActChatAgent(dspy.Module):
    """Interactive ReAct agent that can orchestrate RLM workflows via tools.

    Subclasses ``dspy.Module`` so the agent is:
        - Discoverable in the module graph (``named_sub_modules()``).
        - Optimizable by ``BootstrapFewShot``, ``MIPROv2``, etc.
        - Serializable via ``save()`` / ``load()``.

    The agent is intentionally stateful:
        - Conversation memory is stored as ``dspy.History``.
        - Sandbox state is preserved in one long-lived Modal interpreter session.
        - Optional Modal volume persistence survives across runs.
    """

    def __init__(
        self,
        *,
        react_max_iters: int = 10,
        rlm_max_iterations: int = 30,
        rlm_max_llm_calls: int = 50,
        timeout: int = 900,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
        verbose: bool = False,
        history_max_turns: int | None = None,
        extra_tools: list[Callable[..., Any]] | None = None,
        interpreter: ModalInterpreter | None = None,
    ) -> None:
        super().__init__()
        self.react_max_iters = react_max_iters
        self.rlm_max_iterations = rlm_max_iterations
        self.rlm_max_llm_calls = rlm_max_llm_calls
        self.verbose = verbose
        self.history_max_turns = history_max_turns

        self.interpreter = interpreter or ModalInterpreter(
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            max_llm_calls=rlm_max_llm_calls,
        )

        self.history = dspy.History(messages=[])
        # LRU cache for documents to prevent unbounded growth
        self._document_cache: dict[str, str] = {}
        self._document_access_order: list[str] = []
        self._max_documents = 100
        self.active_alias: str | None = None

        self._started = False
        self._extra_tools: list[Callable[..., Any]] = list(extra_tools or [])

        self.react_tools: list[Callable[..., Any]] = []
        self.react = self._build_agent()

    # -----------------------------------------------------------------
    # Document cache management
    # -----------------------------------------------------------------

    def _get_document(self, alias: str) -> str:
        """Get document with LRU cache tracking."""
        if alias not in self._document_cache:
            raise KeyError(f"Document alias '{alias}' not found")
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)
        return self._document_cache[alias]

    def _set_document(self, alias: str, content: str) -> None:
        """Set document with LRU eviction if needed."""
        if (
            alias not in self._document_cache
            and len(self._document_cache) >= self._max_documents
        ):
            oldest = self._document_access_order.pop(0)
            del self._document_cache[oldest]
        self._document_cache[alias] = content
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)

    def _delete_document(self, alias: str) -> None:
        """Delete document from cache."""
        if alias in self._document_cache:
            del self._document_cache[alias]
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)

    @property
    def documents(self) -> dict[str, str]:
        """Backward-compatible document access."""
        return self._document_cache

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def __enter__(self) -> "RLMReActChatAgent":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.shutdown()
        return False

    def start(self) -> None:
        """Start the underlying Modal interpreter session if needed."""
        if self._started:
            return
        self.interpreter.start()
        self._started = True

    def shutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped."""
        self.interpreter.shutdown()
        self._started = False

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Reset chat history, document cache, and (optionally) sandbox buffers."""
        self.history = dspy.History(messages=[])
        # Clear host-side document state to prevent cross-session leakage.
        docs_count = len(self._document_cache)
        self._document_cache.clear()
        self._document_access_order.clear()
        self.active_alias = None
        if clear_sandbox_buffers:
            # Find clear_buffer tool and call it
            for tool in self.react_tools:
                tool_name = getattr(tool, "name", None) or getattr(
                    tool, "__name__", None
                )
                if tool_name == "clear_buffer":
                    tool() if not isinstance(tool, dspy.Tool) else tool.func()
                    break
        return {
            "status": "ok",
            "history_turns": 0,
            "documents_cleared": docs_count,
            "buffers_cleared": clear_sandbox_buffers,
        }

    def export_session_state(self) -> dict[str, Any]:
        """Export serializable session state for persistence."""
        return {
            "history": list(self.history.messages),
            "documents": dict(self._document_cache),
            "active_alias": self.active_alias,
            "document_access_order": list(self._document_access_order),
        }

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore session state from a previously exported payload."""
        history = state.get("history", [])
        if not isinstance(history, list):
            history = []
        self.history = dspy.History(messages=history)

        documents = state.get("documents", {})
        if isinstance(documents, dict):
            self._document_cache = {
                str(alias): str(content) for alias, content in documents.items()
            }
        else:
            self._document_cache = {}

        access_order = state.get("document_access_order", [])
        if isinstance(access_order, list):
            self._document_access_order = [
                str(alias)
                for alias in access_order
                if str(alias) in self._document_cache
            ]
        else:
            self._document_access_order = []

        # Ensure all cached docs appear in order, even if absent in saved LRU list.
        for alias in self._document_cache:
            if alias not in self._document_access_order:
                self._document_access_order.append(alias)

        active_alias = state.get("active_alias")
        if isinstance(active_alias, str) and active_alias in self._document_cache:
            self.active_alias = active_alias
        else:
            self.active_alias = None

        return {
            "status": "ok",
            "history_turns": len(self.history.messages),
            "documents": len(self._document_cache),
            "active_alias": self.active_alias,
        }

    # -----------------------------------------------------------------
    # DSPy Module forward
    # -----------------------------------------------------------------

    def forward(
        self, *, user_request: str, history: dspy.History | None = None
    ) -> dspy.Prediction:
        """DSPy-compatible forward pass through the ReAct agent.

        This is the method DSPy optimizers call. It delegates to
        ``self.react`` (the ``dspy.ReAct`` sub-module) so the full
        module graph is visible to optimizers and ``save()``/``load()``.
        """
        self.start()
        return self.react(user_request=user_request, history=history or self.history)

    # -----------------------------------------------------------------
    # Public chat API — synchronous
    # -----------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Process one interactive chat turn through the ReAct agent."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        prediction = self.forward(user_request=message)
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        self._append_history(message, assistant_response)

        return {
            "assistant_response": assistant_response,
            "trajectory": getattr(prediction, "trajectory", {}),
            "history_turns": len(self.history.messages),
        }

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterable[StreamEvent]:
        """Yield typed streaming events for one chat turn (sync).

        Delegates to :func:`fleet_rlm.streaming.iter_chat_turn_stream`.
        """
        return _iter_stream(self, message, trace, cancel_check)

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        """Compatibility stream collector for existing CLI/tests."""
        assistant_chunks: list[str] = []
        thought_chunks: list[str] = []
        status_messages: list[str] = []
        trajectory: dict[str, Any] = {}
        assistant_response = ""
        cancelled = False

        for event in self.iter_chat_turn_stream(message=message, trace=trace):
            if event.kind == "assistant_token":
                assistant_chunks.append(event.text)
            elif event.kind == "reasoning_step":
                thought_chunks.append(event.text)
            elif event.kind == "status":
                status_messages.append(event.text)
            elif event.kind == "final":
                assistant_response = event.text
                trajectory = dict(event.payload.get("trajectory", {}) or {})
            elif event.kind == "cancelled":
                cancelled = True
                assistant_response = event.text

        if not assistant_response:
            assistant_response = "".join(assistant_chunks).strip()

        return {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": len(self.history.messages),
            "stream_chunks": assistant_chunks,
            "thought_chunks": thought_chunks if trace else [],
            "status_messages": status_messages,
            "cancelled": cancelled,
        }

    # -----------------------------------------------------------------
    # Public chat API — async (native DSPy async)
    # -----------------------------------------------------------------

    async def achat_turn(self, message: str) -> dict[str, Any]:
        """Async version of chat_turn using ``dspy.ReAct.acall``."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        self.start()
        prediction = await self.react.acall(user_request=message, history=self.history)
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()
        self._append_history(message, assistant_response)

        return {
            "assistant_response": assistant_response,
            "trajectory": getattr(prediction, "trajectory", {}),
            "history_turns": len(self.history.messages),
        }

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield typed streaming events for one chat turn (async).

        Delegates to :func:`fleet_rlm.streaming.aiter_chat_turn_stream`.
        """
        async for event in _aiter_stream(self, message, trace, cancel_check):
            yield event

    # -----------------------------------------------------------------
    # Command dispatch
    # -----------------------------------------------------------------

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a named command to the corresponding agent tool.

        Delegates to :func:`fleet_rlm.commands.execute_command`.
        """
        return await _execute_command(self, command, args)

    # -----------------------------------------------------------------
    # Tool management
    # -----------------------------------------------------------------

    def register_extra_tool(self, tool: Callable[..., Any]) -> dict[str, Any]:
        """Register an additional tool and rebuild the ReAct agent."""
        self._extra_tools.append(tool)
        self.react = self._build_agent()
        return {"status": "ok", "tool_name": getattr(tool, "__name__", str(tool))}

    # -----------------------------------------------------------------
    # Backward-compatible tool delegators
    #
    # These methods delegate to the closure-based tools in react_tools
    # so that existing code calling ``agent.load_document(...)`` etc.
    # continues to work.
    # -----------------------------------------------------------------

    def _get_tool(self, name: str) -> Callable[..., Any]:
        """Look up a tool by name in the current tool list.

        Handles both raw callables (via ``__name__``) and ``dspy.Tool``
        wrappers (via ``.name``).
        """
        for tool in self.react_tools:
            tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if tool_name == name:
                # Return the underlying callable for dspy.Tool wrappers
                return tool.func if isinstance(tool, dspy.Tool) else tool
        raise AttributeError(f"No tool named {name!r}")

    def load_document(self, path: str, alias: str = "active") -> dict[str, Any]:
        """Delegate to the ``load_document`` tool."""
        return self._get_tool("load_document")(path, alias=alias)

    def set_active_document(self, alias: str) -> dict[str, Any]:
        """Delegate to the ``set_active_document`` tool."""
        return self._get_tool("set_active_document")(alias)

    def list_documents(self) -> dict[str, Any]:
        """Delegate to the ``list_documents`` tool."""
        return self._get_tool("list_documents")()

    def list_files(self, path: str = ".", pattern: str = "**/*") -> dict[str, Any]:
        """Delegate to the ``list_files`` tool."""
        return self._get_tool("list_files")(path=path, pattern=pattern)

    def read_file_slice(
        self, path: str, start_line: int = 1, num_lines: int = 100
    ) -> dict[str, Any]:
        """Delegate to the ``read_file_slice`` tool."""
        return self._get_tool("read_file_slice")(
            path, start_line=start_line, num_lines=num_lines
        )

    def find_files(
        self, pattern: str, path: str = ".", include: str = ""
    ) -> dict[str, Any]:
        """Delegate to the ``find_files`` tool."""
        return self._get_tool("find_files")(pattern, path=path, include=include)

    def chunk_host(
        self,
        strategy: str,
        alias: str = "active",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Delegate to the ``chunk_host`` tool."""
        return self._get_tool("chunk_host")(
            strategy, alias=alias, size=size, overlap=overlap, pattern=pattern
        )

    def chunk_sandbox(
        self,
        strategy: str,
        variable_name: str = "active_document",
        buffer_name: str = "chunks",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Delegate to the ``chunk_sandbox`` tool."""
        return self._get_tool("chunk_sandbox")(
            strategy,
            variable_name=variable_name,
            buffer_name=buffer_name,
            size=size,
            overlap=overlap,
            pattern=pattern,
        )

    def parallel_semantic_map(
        self,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Delegate to the ``parallel_semantic_map`` tool."""
        return self._get_tool("parallel_semantic_map")(
            query,
            chunk_strategy=chunk_strategy,
            max_chunks=max_chunks,
            buffer_name=buffer_name,
        )

    def analyze_long_document(
        self,
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Delegate to the ``analyze_long_document`` tool."""
        return self._get_tool("analyze_long_document")(
            query, alias=alias, include_trajectory=include_trajectory
        )

    def summarize_long_document(
        self,
        focus: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Delegate to the ``summarize_long_document`` tool."""
        return self._get_tool("summarize_long_document")(
            focus, alias=alias, include_trajectory=include_trajectory
        )

    def extract_from_logs(
        self,
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Delegate to the ``extract_from_logs`` tool."""
        return self._get_tool("extract_from_logs")(
            query, alias=alias, include_trajectory=include_trajectory
        )

    def read_buffer(self, name: str) -> dict[str, Any]:
        """Delegate to the ``read_buffer`` tool."""
        return self._get_tool("read_buffer")(name)

    def clear_buffer(self, name: str = "") -> dict[str, Any]:
        """Delegate to the ``clear_buffer`` tool."""
        return self._get_tool("clear_buffer")(name)

    def save_buffer_to_volume(self, name: str, path: str) -> dict[str, Any]:
        """Delegate to the ``save_buffer_to_volume`` tool."""
        return self._get_tool("save_buffer_to_volume")(name, path)

    def load_text_from_volume(self, path: str, alias: str = "active") -> dict[str, Any]:
        """Delegate to the ``load_text_from_volume`` tool."""
        return self._get_tool("load_text_from_volume")(path, alias=alias)

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _build_agent(self) -> dspy.Module:
        self.react_tools = build_tool_list(self, self._extra_tools)
        return dspy.ReAct(
            signature=RLMReActChatSignature,
            tools=list(self.react_tools),
            max_iters=self.react_max_iters,
        )

    def _append_history(self, user_request: str, assistant_response: str) -> None:
        messages = list(self.history.messages)
        messages.append(
            {
                "user_request": user_request,
                "assistant_response": assistant_response,
            }
        )
        if self.history_max_turns is not None and self.history_max_turns > 0:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)
