"""Thin DSPy Module wrappers for the interactive ReAct chat agent.

This module provides minimal, pure-DSPy agent definitions that separate
the inference graph (``dspy.ReAct``) from runtime orchestration concerns
such as interpreter lifecycle, session state, and streaming.
"""

from __future__ import annotations

from typing import Any, cast

import dspy

from .signatures import RLMReActChatSignature


def _is_finish_only_trajectory(trajectory: dict[str, Any]) -> bool:
    """Return True when the very first ReAct step immediately finishes."""
    return trajectory.get("tool_name_0") == "finish" and "tool_name_1" not in trajectory


def _extract_finish_only_payload(
    *,
    signature: type[dspy.Signature],
    trajectory: dict[str, Any],
) -> dict[str, str]:
    """Map the first finish thought onto the signature output fields."""
    thought = str(trajectory.get("thought_0", ""))
    output_names = list(signature.output_fields.keys())
    if not output_names:
        return {}

    payload = {name: "" for name in output_names}
    payload[output_names[0]] = thought
    return payload


def _patch_finish_only_extract(
    react: Any,
    *,
    signature: type[dspy.Signature],
) -> Any:
    """Skip ReAct's extract LM call when the first step is an immediate finish."""
    extract_module = getattr(react, "extract", None)
    react_module = getattr(react, "react", None)
    sync_helper = getattr(react, "_call_with_potential_trajectory_truncation", None)
    if extract_module is not None and callable(sync_helper):

        def _patched_sync(module: Any, trajectory: dict[str, Any], **input_args: Any) -> Any:
            if module is react_module:
                prediction = sync_helper(module, trajectory, **input_args)
                tool_name = getattr(prediction, "next_tool_name", "")
                if tool_name not in getattr(react, "tools", {}):
                    raise ValueError(f"Agent failed to select a valid tool: {tool_name!r}")
                return prediction
            if module is extract_module and _is_finish_only_trajectory(trajectory):
                return _extract_finish_only_payload(signature=signature, trajectory=trajectory)
            return sync_helper(module, trajectory, **input_args)

        react._call_with_potential_trajectory_truncation = _patched_sync

    async_helper = getattr(react, "_async_call_with_potential_trajectory_truncation", None)
    if extract_module is not None and callable(async_helper):

        async def _patched_async(module: Any, trajectory: dict[str, Any], **input_args: Any) -> Any:
            if module is react_module:
                prediction = await async_helper(module, trajectory, **input_args)
                tool_name = getattr(prediction, "next_tool_name", "")
                if tool_name not in getattr(react, "tools", {}):
                    raise ValueError(f"Agent failed to select a valid tool: {tool_name!r}")
                return prediction
            if module is extract_module and _is_finish_only_trajectory(trajectory):
                return _extract_finish_only_payload(signature=signature, trajectory=trajectory)
            return await async_helper(module, trajectory, **input_args)

        react._async_call_with_potential_trajectory_truncation = _patched_async

    return react


def _normalize_prompt_text(value: Any) -> str:
    """Collapse prompt text to a single space-delimited line."""
    return " ".join(str(value).split())


def _format_prompt_default(value: Any) -> str:
    """Render a short default-value literal for compact tool prompts."""
    if value is None:
        return "null"
    return repr(value)


def _format_prompt_schema_type(schema: Any) -> str:
    """Render a short type label from a DSPy tool argument schema."""
    if not isinstance(schema, dict) or not schema:
        return "any"

    if "anyOf" in schema:
        labels: list[str] = []
        for option in schema["anyOf"]:
            label = _format_prompt_schema_type(option)
            if label not in labels:
                labels.append(label)
        return "|".join(labels) or "any"

    schema_type = schema.get("type")
    if schema_type == "array":
        item_type = _format_prompt_schema_type(schema.get("items"))
        return f"list[{item_type}]"
    if schema_type == "object" and schema.get("additionalProperties") is True:
        return "dict[str, any]"
    if isinstance(schema_type, str):
        return schema_type
    return "any"


def _format_prompt_tool_args(tool: Any) -> str:
    """Render compact tool arguments as name:type=default fragments."""
    args = getattr(tool, "args", {})
    if not isinstance(args, dict) or not args:
        return ""

    parts: list[str] = []
    for name, schema in args.items():
        type_name = _format_prompt_schema_type(schema)
        part = f"{name}:{type_name}"
        if isinstance(schema, dict) and "default" in schema:
            part = f"{part}={_format_prompt_default(schema['default'])}"
        parts.append(part)
    return ", ".join(parts)


def _format_prompt_tool(tool: Any) -> str:
    """Render a compact FleetAgent tool prompt line."""
    tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
    description = _normalize_prompt_text(
        "Stop and return the final response."
        if tool_name == "finish"
        else getattr(tool, "desc", getattr(tool, "__doc__", ""))
    )
    signature = f"{tool_name}({_format_prompt_tool_args(tool)})"
    return f"- {signature}: {description}" if description else f"- {signature}"


def _build_compact_react_instructions(react: Any) -> str:
    """Build a compact ReAct instruction block for FleetAgent."""
    signature = getattr(react, "signature", None)
    if signature is None:
        return ""

    input_names = ", ".join(f"`{name}`" for name in signature.input_fields)
    output_names = ", ".join(f"`{name}`" for name in signature.output_fields)
    tool_lines = [_format_prompt_tool(tool) for tool in getattr(react, "tools", {}).values()]

    lines = [
        f"You are an agent. Use tools only when needed to produce {output_names} from {input_names}.",
        "At each step output next_thought, next_tool_name, and next_tool_args.",
        "Tool observations are appended to trajectory.",
        "next_tool_args must be a JSON object for the selected tool.",
        "Available tools:",
        *tool_lines,
        "If you choose finish on the first step without using any tool, write next_thought as the exact final response to send to the user.",
    ]
    return "\n".join(lines)


def _build_compact_react_signature(react: Any) -> type[dspy.Signature] | None:
    """Build a compact internal ReAct signature for FleetAgent."""
    signature = getattr(react, "signature", None)
    if signature is None:
        return None

    signature_builder = cast(Any, dspy.Signature)
    return (
        signature_builder({**signature.input_fields}, _build_compact_react_instructions(react))
        .append("trajectory", dspy.InputField(), type_=str)
        .append("next_thought", dspy.OutputField(), type_=str)
        .append("next_tool_name", dspy.OutputField(), type_=str)
        .append("next_tool_args", dspy.OutputField(), type_=dict[str, Any])
    )


def _patch_finish_only_prompt(react: Any) -> Any:
    """Replace the default ReAct prompt with a compact FleetAgent-specific version."""
    react_module = getattr(react, "react", None)
    compact_signature = _build_compact_react_signature(react)
    if react_module is None or compact_signature is None or not hasattr(react_module, "signature"):
        return react

    react_module.signature = compact_signature
    return react


class FleetAgentSignature(dspy.Signature):
    """Simplified ReAct chat signature for FleetAgent."""

    chat_history: dspy.History = dspy.InputField(desc="Prior conversation turns (keys: user_message, response)")
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
        self.react = _patch_finish_only_prompt(self.react)
        self.react = _patch_finish_only_extract(
            self.react,
            signature=FleetAgentSignature,
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
