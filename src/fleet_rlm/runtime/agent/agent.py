"""Thin DSPy Module wrappers for the interactive ReAct chat agent.

This module provides a minimal, pure-DSPy agent definition that separates
the inference graph from runtime orchestration concerns such as
interpreter lifecycle, session state, and streaming.

``FleetAgent`` subclasses the canonical :class:`dspy.ReAct` so it inherits the
upstream planner/extract predictors, native trajectory truncation, and
``Literal``-constrained tool selection. A pair of thin compatibility shims
(:pyattr:`planner` and :meth:`async_planner_step`) let :class:`AgentRuntime`
drive the planner one step at a time for token streaming.
"""

from __future__ import annotations

import logging
from typing import Any

import dspy
from dspy.utils.exceptions import ContextWindowExceededError

logger = logging.getLogger(__name__)


class FleetAgentSignature(dspy.Signature):
    """Respond to the user, using tools only when they are needed.

    When you call the `finish` tool, write `next_thought` as the exact, complete
    final response to send to the user. The runtime streams that text directly
    and skips a separate extraction step, so `next_thought` on the finishing turn
    must be the full user-facing answer rather than internal reasoning.
    """

    chat_history: dspy.History = dspy.InputField(desc="Prior conversation turns (keys: user_message, response)")
    user_message: str = dspy.InputField(desc="Current user message")
    response: str = dspy.OutputField(desc="Agent response to the user")


class FleetAgent(dspy.ReAct):
    """ReAct chat agent built on the canonical :class:`dspy.ReAct`.

    Inherits upstream behaviour: the planner predictor ``self.react``, the
    extractor ``self.extract``, native ``truncate_trajectory`` /
    ``_format_trajectory`` helpers, and a ``finish`` tool with
    ``Literal``-constrained tool selection. The :pyattr:`planner` alias and
    :meth:`async_planner_step` shim expose the step-by-step interface the
    streaming runtime drives to interleave token streaming and tool execution.
    """

    def __init__(
        self,
        *,
        tools: list[Any],
        max_iters: int = 10,
    ) -> None:
        super().__init__(FleetAgentSignature, tools=list(tools), max_iters=max_iters)

    @property
    def planner(self) -> dspy.Predict:
        """Alias for the upstream ReAct planner predictor.

        The streaming runtime introspects ``planner`` to decide whether a
        program can be driven step-by-step.
        """
        return self.react

    async def async_planner_step(self, trajectory: dict[str, Any], **input_args: Any) -> dspy.Prediction:
        """Run one planner step with context-window truncation retries.

        Mirrors the upstream truncation loop but is exposed publicly so the
        runtime can interleave streaming and sandbox tool execution into the
        cognitive loop.
        """
        for _ in range(3):
            try:
                prediction = await self.react.acall(
                    **input_args,
                    trajectory=self._format_trajectory(trajectory),
                )
                tool_name = getattr(prediction, "next_tool_name", "")
                if tool_name and tool_name not in self.tools:
                    raise ValueError(f"Agent failed to select a valid tool: {tool_name!r}")
                return prediction
            except ContextWindowExceededError:
                logger.warning("Trajectory exceeded the context window, truncating the oldest tool call information.")
                trajectory = self.truncate_trajectory(trajectory)
        raise ValueError("The context window was exceeded even after 3 attempts to truncate the trajectory.")
