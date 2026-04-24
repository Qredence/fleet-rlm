"""Thin DSPy Module wrappers for the interactive ReAct chat agent.

This module provides minimal, pure-DSPy agent definitions that separate
the inference graph (``dspy.ReAct``) from runtime orchestration concerns
such as interpreter lifecycle, session state, and streaming.
"""

from __future__ import annotations

import dspy

from .signatures import RLMReActChatSignature


class FleetAgentSignature(dspy.Signature):
    """Simplified ReAct chat signature for FleetAgent."""

    chat_history: dspy.History = dspy.InputField(
        desc="Prior conversation turns (keys: user_message, response)"
    )
    user_message: str = dspy.InputField(desc="Current user message")
    response: str = dspy.OutputField(desc="Agent response to the user")


class FleetAgent(dspy.Module):
    """Simplified DSPy ReAct agent wrapping FleetAgentSignature.

    The module graph is trivial (``self.react`` is the only submodule) so
    optimizers and ``save()`` / ``load()`` work without custom logic.

    No runtime or session side effects — lifecycle concerns belong in the
    surrounding runtime class.
    """

    def __init__(
        self,
        *,
        tools: list,
        max_iters: int = 10,
    ) -> None:
        super().__init__()
        self.react = dspy.ReAct(
            signature=FleetAgentSignature,
            tools=list(tools),
            max_iters=max_iters,
        )

    def forward(
        self,
        *,
        chat_history: dspy.History,
        user_message: str,
    ) -> dspy.Prediction:
        """DSPy-compatible forward pass through the ReAct agent."""
        return self.react(
            chat_history=chat_history,
            user_message=user_message,
        )


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
        _ = max_iters
        return self.react(
            user_request=user_request,
            history=history,
            core_memory=core_memory,
        )
