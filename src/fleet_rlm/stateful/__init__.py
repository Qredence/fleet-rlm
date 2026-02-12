"""Stateful execution components.

Provides persistent state management for RLM workflows:
- StatefulSandboxManager: Wrapper with execution history
- AgentStateManager: Higher-level state with analysis/scripts
"""

from __future__ import annotations

from .agent import AgentStateManager, AnalysisResult, CodeScript
from .sandbox import ExecutionRecord, SandboxResult, StatefulSandboxManager

__all__ = [
    "AgentStateManager",
    "AnalysisResult",
    "CodeScript",
    "ExecutionRecord",
    "SandboxResult",
    "StatefulSandboxManager",
]
