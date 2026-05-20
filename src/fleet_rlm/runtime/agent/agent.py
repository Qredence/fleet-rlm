"""Thin DSPy Module wrappers for the interactive ReAct chat agent.

This module provides minimal, pure-DSPy agent definitions that separate
the inference graph from runtime orchestration concerns such as
interpreter lifecycle, session state, and streaming.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import dspy
from dspy.utils.exceptions import ContextWindowExceededError

logger = logging.getLogger(__name__)


class FleetAgentSignature(dspy.Signature):
    """Simplified ReAct chat signature for FleetAgent."""

    chat_history: dspy.History = dspy.InputField(desc="Prior conversation turns (keys: user_message, response)")
    user_message: str = dspy.InputField(desc="Current user message")
    response: str = dspy.OutputField(desc="Agent response to the user")


class FleetAgent(dspy.Module):
    """A custom DSPy Module implementing ReAct cleanly for streaming.

    This module instantiates its own `dspy.Predict` (for planning) and `dspy.ChainOfThought`
    (for extraction) with custom instructions optimized for the RLM system.
    It exposes `async_planner_step` and `async_extract_step` to allow the AgentRuntime
    to weave external effects (e.g. streaming, sandbox tool execution) directly
    into the cognitive loop.
    """

    def __init__(
        self,
        *,
        tools: list[Any],
        max_iters: int = 10,
    ) -> None:
        super().__init__()
        self.signature = FleetAgentSignature
        self.max_iters = max_iters

        self.tools = {getattr(t, "name", getattr(t, "__name__", str(t))): t for t in tools}
        if "finish" not in self.tools:
            self.tools["finish"] = dspy.Tool(
                func=lambda: "Completed.",
                name="finish",
                desc="Stop and return the final response.",
                args={},
            )

        input_names = ", ".join(f"`{name}`" for name in self.signature.input_fields)
        output_names = ", ".join(f"`{name}`" for name in self.signature.output_fields)

        tool_lines = []
        for name, tool in self.tools.items():
            desc = getattr(tool, "desc", getattr(tool, "__doc__", "No description available."))
            tool_lines.append(f"- {name}: {desc}")

        instructions = "\n".join([
            f"You are an agent. Use tools only when needed to produce {output_names} from {input_names}.",
            "At each step output next_thought, next_tool_name, and next_tool_args.",
            "Tool observations are appended to trajectory.",
            "next_tool_args must be a JSON object for the selected tool.",
            "Available tools:",
            *tool_lines,
            "If you choose finish on the first step without using any tool, write next_thought as the exact final response to send to the user.",
        ])

        signature_builder = cast(Any, dspy.Signature)
        self.react_signature = (
            signature_builder({**self.signature.input_fields}, instructions)
            .append("trajectory", dspy.InputField(), type_=str)
            .append("next_thought", dspy.OutputField(), type_=str)
            .append("next_tool_name", dspy.OutputField(), type_=str)
            .append("next_tool_args", dspy.OutputField(), type_=dict[str, Any])
        )

        self.fallback_signature = signature_builder(
            {**self.signature.input_fields, **self.signature.output_fields}, self.signature.instructions
        ).append("trajectory", dspy.InputField(), type_=str)

        self.planner = dspy.Predict(self.react_signature)
        self.extract = dspy.ChainOfThought(self.fallback_signature)

    def _format_trajectory(self, trajectory: dict[str, Any]) -> str:
        adapter = cast(Any, getattr(dspy.settings, "adapter", None) or dspy.ChatAdapter())
        signature_builder = cast(Any, dspy.Signature)
        trajectory_signature = signature_builder(f"{', '.join(trajectory.keys())} -> x")
        return adapter.format_user_message_content(trajectory_signature, trajectory)

    def truncate_trajectory(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        """Truncates the oldest tool call information from the trajectory."""
        keys = list(trajectory.keys())
        if len(keys) < 4:
            raise ValueError(
                "The trajectory is too long so your prompt exceeded the context window, "
                "but the trajectory cannot be truncated because it only has one tool call."
            )
        for key in keys[:4]:
            trajectory.pop(key)
        return trajectory

    async def async_planner_step(self, trajectory: dict[str, Any], **input_args: Any) -> dspy.Prediction:
        """Call the planner with truncation retry logic."""
        for _ in range(3):
            try:
                prediction = await self.planner.acall(
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

    async def async_extract_step(self, trajectory: dict[str, Any], **input_args: Any) -> dspy.Prediction:
        """Call the extractor with truncation retry logic."""
        for _ in range(3):
            try:
                return await self.extract.acall(**input_args, trajectory=self._format_trajectory(trajectory))
            except ContextWindowExceededError:
                logger.warning("Trajectory exceeded the context window, truncating the oldest tool call information.")
                trajectory = self.truncate_trajectory(trajectory)
        raise ValueError("The context window was exceeded even after 3 attempts to truncate the trajectory.")

    def forward(self, **input_args: Any) -> dspy.Prediction:
        """Synchronous forward pass through the ReAct agent."""
        trajectory: dict[str, Any] = {}
        last_tool_name: str | None = None
        last_thought: str | None = None
        for idx in range(self.max_iters):
            try:

                def sync_call(module, traj, **kwargs):
                    for _ in range(3):
                        try:
                            return module(**kwargs, trajectory=self._format_trajectory(traj))
                        except ContextWindowExceededError:
                            traj = self.truncate_trajectory(traj)
                    raise ValueError("Context window exceeded")

                pred = sync_call(self.planner, trajectory, **input_args)
                tool_name = getattr(pred, "next_tool_name", "")
                if tool_name and tool_name not in self.tools:
                    raise ValueError(f"Agent failed to select a valid tool: {tool_name!r}")
            except ValueError as err:
                logger.warning(f"Ending the trajectory: {err}")
                break

            trajectory[f"thought_{idx}"] = pred.next_thought
            trajectory[f"tool_name_{idx}"] = tool_name
            trajectory[f"tool_args_{idx}"] = pred.next_tool_args

            last_tool_name = tool_name
            last_thought = pred.next_thought

            try:
                tool = self.tools[tool_name]
                if hasattr(tool, "func"):
                    func = tool.func
                elif callable(tool):
                    func = tool
                else:
                    func = getattr(tool, "forward", None)
                if func is None:
                    raise ValueError(f"Tool {tool_name} is not callable")
                func = cast(Any, func)
                trajectory[f"observation_{idx}"] = func(**pred.next_tool_args)
            except Exception as err:
                trajectory[f"observation_{idx}"] = f"Execution error in {tool_name}: {err}"

            if tool_name == "finish":
                break

        # Check for terminal step shortcut
        if last_tool_name == "finish" or not last_tool_name:
            return dspy.Prediction(trajectory=trajectory, response=str(last_thought or ""))

        def sync_extract(module, traj, **kwargs):
            for _ in range(3):
                try:
                    return module(**kwargs, trajectory=self._format_trajectory(traj))
                except ContextWindowExceededError:
                    traj = self.truncate_trajectory(traj)
            raise ValueError("Context window exceeded")

        extract = sync_extract(self.extract, trajectory, **input_args)
        return dspy.Prediction(trajectory=trajectory, **extract)
