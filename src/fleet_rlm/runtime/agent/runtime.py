"""Agent runtime — owns interpreter, session state, tools, and execution context.

This module provides:

- ``AgentRuntime`` — simplified runtime using ``FleetAgent`` + ``discover_tools()``.
  This is the primary class for all new code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.runtime.tools import discover_tools

if TYPE_CHECKING:
    from .agent import FleetAgent


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
