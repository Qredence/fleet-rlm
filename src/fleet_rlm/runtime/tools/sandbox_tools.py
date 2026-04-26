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
    """Execute Python code in the Daytona sandbox and return the SUBMIT() payload.

    The code runs inside an isolated Daytona workspace that provides
    ``SUBMIT()``, ``add_buffer()``, ``read_buffer()``, ``clear_buffer()``,
    and volume-access helpers.  Use ``variables`` to inject Python values
    into the sandbox scope before execution.

    Prefer this tool when data is already available in the sandbox and Python
    should orchestrate batching or aggregation. Use ``llm_query_batched()`` in
    the submitted code for many lightweight semantic prompts, and use
    ``sub_rlm_batched()`` for multiple independent recursive child RLM tasks.

    Args:
        code: Python source code to execute in the sandbox.
        variables: Optional mapping of variable names to values injected into
            the sandbox execution scope before running *code*.
        timeout: Maximum seconds to wait for sandbox execution. Defaults to 30.

    Returns:
        Dictionary produced by the ``SUBMIT()`` call in the sandbox code,
        augmented with ``status``, ``stdout``, and ``stderr`` fields.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime`` interpreter.
    """
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
    """Run a multi-pass recursive analysis with decomposition, verification, and self-repair.

    Use this for complex tasks that benefit from iterative refinement:
    multi-step analysis, large document processing, or multi-faceted
    research where a single delegate_to_rlm call is insufficient.

    The orchestrator runs a bounded loop: assemble context from prior
    evidence, plan decomposition into subqueries, execute them via
    child RLM sandboxes, verify the aggregated results for contradictions
    or gaps, and reflect on whether to finalize, recurse deeper, or
    self-repair.

    Args:
        query: The complex task or question to analyze recursively.
        context: Optional initial context to seed the first pass.
        max_passes: Maximum number of recursive passes (default 3).

    Returns:
        Dictionary with ``status``, ``answer``, and ``passes`` count.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime`` interpreter.
    """
    raise RuntimeError(
        "recursive_workspace requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["execute_code", "recursive_workspace"]
