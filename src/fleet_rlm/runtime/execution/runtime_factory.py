"""Runtime module factory for ReAct agent long-context operations.

This module provides lazy-loading access to cached DSPy modules that handle
long-context operations like document analysis, summarization, and memory management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import dspy

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def get_runtime_module(agent: RLMReActChatAgent, name: str) -> dspy.Module:
    """Return a cached long-context runtime module by name.

    Lazily constructs and caches DSPy modules for long-context operations.
    Modules are created with the agent's interpreter and configuration.

    Args:
        agent: The RLMReActChatAgent instance requesting the module
        name: The module name (e.g., 'analyze_long_document', 'grounded_answer')

    Returns:
        The requested DSPy module instance

    Raises:
        ValueError: If the module name is not recognized
    """
    # Import lazily to avoid circular dependency:
    # core.agent → core.execution.runtime_factory → core.models.rlm_runtime_modules
    # → core.agent.signatures → core.agent.__init__ → chat_agent → runtime_factory
    from fleet_rlm.runtime.models.rlm_runtime_modules import (
        build_runtime_module_config,
        get_or_build_runtime_module,
    )

    return get_or_build_runtime_module(
        agent._runtime_modules,
        name,
        config=build_runtime_module_config(
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        ),
    )
