"""ReAct chat agent and tools.

This module provides the RLM ReAct chat system with:
- RLMReActChatAgent: Conversational agent with tool use
- Streaming support for real-time updates
- Tool definitions for document handling, filesystem, analysis
"""

from __future__ import annotations

from .agent import RLMReActChatAgent, RLMReActChatSignature
from .commands import COMMAND_DISPATCH, execute_command
from .streaming import aiter_chat_turn_stream, iter_chat_turn_stream
from .tools import build_tool_list, list_react_tool_names

__all__ = [
    "RLMReActChatAgent",
    "RLMReActChatSignature",
    "COMMAND_DISPATCH",
    "execute_command",
    "aiter_chat_turn_stream",
    "iter_chat_turn_stream",
    "build_tool_list",
    "list_react_tool_names",
]
