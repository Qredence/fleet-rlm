"""Thin DSPy Module wrappers for the interactive ReAct chat agent.

This module provides a minimal, pure-DSPy agent definition that separates
the inference graph from runtime orchestration concerns such as
interpreter lifecycle, session state, and streaming.

``FleetAgent`` subclasses the canonical :class:`dspy.ReAct` so it inherits the
upstream planner/extract predictors, native trajectory truncation, and
``Literal``-constrained tool selection. Streaming is handled externally by
wrapping the whole program with ``dspy.streamify`` (see
:mod:`fleet_rlm.runtime.agent.runtime_streaming`).
"""

from __future__ import annotations

from typing import Any

import dspy


class FleetAgentSignature(dspy.Signature):
    """Respond to the user, using tools only when they are needed."""

    chat_history: dspy.History = dspy.InputField(desc="Prior conversation turns (keys: user_message, response)")
    user_message: str = dspy.InputField(desc="Current user message")
    response: str = dspy.OutputField(desc="Agent response to the user")


class FleetAgent(dspy.ReAct):
    """ReAct chat agent built on the canonical :class:`dspy.ReAct`.

    Inherits upstream behaviour: the planner predictor ``self.react``, the
    extractor ``self.extract``, native ``truncate_trajectory`` /
    ``_format_trajectory`` helpers, and a ``finish`` tool with
    ``Literal``-constrained tool selection.
    """

    def __init__(
        self,
        *,
        tools: list[Any],
        max_iters: int = 10,
    ) -> None:
        super().__init__(FleetAgentSignature, tools=list(tools), max_iters=max_iters)
