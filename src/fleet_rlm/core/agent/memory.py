"""Core memory management mixin for ReAct agent.

This module provides the CoreMemoryMixin class which handles persistent
memory blocks (Persona, Human, Scratchpad) that define the agent's
identity and context.

Core Memory (Tier 1) is host-side only, with persistence via
export_session_state / import_session_state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # No imports needed for type hints here


class CoreMemoryMixin:
    """Mixin providing core memory management for ReAct agents.

    Core memory consists of named blocks (persona, human, scratchpad)
    with configurable character limits to prevent context window explosion.

    Attributes:
        _core_memory: Dict mapping block names to content strings
        _core_memory_limits: Dict mapping block names to character limits
    """

    # Default core memory blocks - subclasses can override
    _DEFAULT_CORE_MEMORY: dict[str, str] = {
        "persona": "I am a helpful AI assistant focused on writing high-quality code.",
        "human": "The user is a developer working on this project.",
        "scratchpad": "No current active task.",
    }

    # Default character limits per block
    _DEFAULT_MEMORY_LIMITS: dict[str, int] = {
        "persona": 2000,
        "human": 2000,
        "scratchpad": 1000,
    }

    def _init_core_memory(self) -> None:
        """Initialize core memory with defaults.

        Called during __init__ to set up the core memory structures.
        """
        self._core_memory: dict[str, str] = dict(self._DEFAULT_CORE_MEMORY)
        self._core_memory_limits: dict[str, int] = dict(self._DEFAULT_MEMORY_LIMITS)

    def core_memory_append(self, section: str, content: str) -> str:
        """Append text to a specific Core Memory block.

        Args:
            section: The block name (e.g., 'scratchpad', 'human', 'persona')
            content: The text to append

        Returns:
            Success message or error description
        """
        if section not in self._core_memory:
            return f"Error: Core memory block '{section}' does not exist. Available: {list(self._core_memory.keys())}"

        current_len = len(self._core_memory[section])
        new_len = current_len + len(content) + 1  # +1 for newline
        limit = self._core_memory_limits.get(section, 1000)

        if new_len > limit:
            return f"Error: Appending content would exceed limit for '{section}' ({new_len} > {limit}). Please summarize or replace."

        self._core_memory[section] += f"\n{content}"
        return f"Appended to '{section}'. New content length: {len(self._core_memory[section])} chars."

    def core_memory_replace(self, section: str, content: str) -> str:
        """Replace the entire content of a Core Memory block.

        Args:
            section: The block name to replace
            content: The new content

        Returns:
            Success message or error description
        """
        if section not in self._core_memory:
            return f"Error: Core memory block '{section}' does not exist. Available: {list(self._core_memory.keys())}"

        limit = self._core_memory_limits.get(section, 1000)
        if len(content) > limit:
            return f"Error: Content exceeds limit for '{section}' ({len(content)} > {limit})."

        self._core_memory[section] = content
        return f"Updated block '{section}'."

    def fmt_core_memory(self) -> str:
        """Format the core memory blocks for the prompt.

        Returns:
            Formatted string with XML-style tags for each block
        """
        blocks = []
        for key, value in self._core_memory.items():
            blocks.append(f"<{key}>\n{value}\n</{key}>")
        return "\n\n".join(blocks)

    def get_core_memory_snapshot(self) -> dict[str, str]:
        """Return a defensive copy of core memory state.

        Returns:
            Copy of the core memory dict
        """
        return self._core_memory.copy()

    def set_core_memory(self, memory: dict[str, Any] | None) -> None:
        """Set core memory from an external dict (e.g., import_session_state).

        Args:
            memory: Dict mapping block names to content
        """
        if isinstance(memory, dict):
            self._core_memory = memory

    def get_core_memory_keys(self) -> list[str]:
        """Return list of core memory block names.

        Returns:
            List of block names
        """
        return list(self._core_memory.keys())
