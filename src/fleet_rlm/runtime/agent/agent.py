"""Thin DSPy Module wrapper for the interactive ReAct chat agent.

This module provides a minimal, pure-DSPy agent definition that separates
the inference graph (``dspy.ReAct``) from runtime orchestration concerns
such as interpreter lifecycle, session state, and streaming.
"""

from __future__ import annotations

import dspy

from .signatures import RLMReActChatSignature


class RLMReActAgent(dspy.Module):
    """Pure DSPy ReAct agent with no runtime or session side effects.

    The module graph is trivial (``self.react`` is the only submodule) so
    optimizers and ``save()`` / ``load()`` work without custom logic.
    """

    def __init__(
        self,
        *,
        signature: type[dspy.Signature] = RLMReActChatSignature,
        tools: list[dspy.Tool],
        max_iters: int = 10,
    ) -> None:
        super().__init__()
        self.react = dspy.ReAct(
            signature=signature,
            tools=list(tools),
            max_iters=max_iters,
        )

    def forward(
        self,
        *,
        user_request: str,
        history: dspy.History,
        core_memory: str,
        max_iters: int,
    ) -> dspy.Prediction:
        """DSPy-compatible forward pass through the ReAct agent."""
        return self.react(
            user_request=user_request,
            history=history,
            core_memory=core_memory,
            max_iters=max_iters,
        )
