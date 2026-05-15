"""Simplified sandbox code-execution tool for the fleet tool registry.

Exposes a single module-level ``execute_code`` function marked with
``@tool_fn`` so that ``discover_tools()`` can collect it.  The concrete
execution is delegated to the Daytona interpreter held by ``AgentRuntime``;
calling this function directly (without a bound runtime) raises
``RuntimeError``.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


@tool_fn
def execute_code(
    code: str,
    variables: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Execute Python code in the Daytona sandbox. Use llm_query_batched() inside the code for many lightweight semantic prompts,
    sub_rlm_batched() for recursive child tasks."""
    raise RuntimeError(
        "execute_code requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def recursive_workspace(
    query: str,
    context: str = "",
    max_passes: int = 3,
) -> dict[str, Any]:
    """Run a multi-pass recursive Daytona workspace analysis for complex tasks."""
    raise RuntimeError(
        "recursive_workspace requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["execute_code", "recursive_workspace"]
