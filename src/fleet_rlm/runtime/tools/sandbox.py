"""Sandbox, RLM, memory, buffer, and volume tool definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .sandbox_delegate_tools import build_rlm_delegate_tools
from .sandbox_memory_tools import build_memory_intelligence_tools
from .sandbox_storage_tools import build_storage_tools

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def build_sandbox_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """
    tools: list[Any] = []
    tools.extend(build_rlm_delegate_tools(agent))
    tools.extend(build_memory_intelligence_tools(agent))
    tools.extend(build_storage_tools(agent))
    return tools
