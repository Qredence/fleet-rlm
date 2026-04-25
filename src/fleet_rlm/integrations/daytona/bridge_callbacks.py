"""Host callback dispatch for Daytona-backed RLM bridge execution."""

from __future__ import annotations

import json
from typing import AbstractSet, Any, Callable

from dspy.primitives import CodeInterpreterError

from .interpreter_assets import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
)


def reject_unsupported_recursive_callbacks(
    interpreter: Any,
    code: str,
    *,
    callbacks: tuple[str, ...] = _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
) -> None:
    """Reject agent-level recursive callbacks from raw sandbox code."""
    _ = interpreter
    for callback_name in callbacks:
        if f"{callback_name}(" not in code:
            continue
        raise CodeInterpreterError(
            f"{callback_name}() is not available inside Daytona sandbox code. "
            "Use llm_query()/llm_query_batched() for semantic callbacks; "
            "recursive rlm_query* tools are agent-level only."
        )


def bridge_tools(
    interpreter: Any,
    *,
    native_tool_names: AbstractSet[str] = _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
) -> dict[str, Callable[..., Any]]:
    """Return host callbacks exposed to sandbox bridge wrappers."""
    tools = {
        name: tool
        for name, tool in interpreter._tools.items()
        if name not in native_tool_names
    }
    if "llm_query" not in tools:
        tools["llm_query"] = interpreter.llm_query
    if "llm_query_batched" not in tools:
        tools["llm_query_batched"] = interpreter.llm_query_batched
    if "sub_rlm" not in tools and hasattr(interpreter, "sub_rlm"):
        tools["sub_rlm"] = interpreter.sub_rlm
    if "sub_rlm_batched" not in tools and hasattr(interpreter, "sub_rlm_batched"):
        tools["sub_rlm_batched"] = interpreter.sub_rlm_batched
    if "fetch_document_text" not in tools:
        from fleet_rlm.runtime.tools.document_tools import fetch_document_text

        tools["fetch_document_text"] = fetch_document_text
    return tools


def requires_bridge(
    interpreter: Any,
    code: str,
    tools: dict[str, Callable[..., Any]],
) -> bool:
    """Return whether code mentions one of the bridged callback names."""
    _ = interpreter
    return any(f"{tool_name}(" in code for tool_name in tools)


def invoke_tool(
    interpreter: Any,
    name: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> Any:
    """Invoke a host-side callback requested by sandbox bridge code."""
    try:
        if name == "llm_query":
            prompt = _callback_arg(args, kwargs, 0, "prompt", "")
            value = interpreter.llm_query(str(prompt))
        elif name == "llm_query_batched":
            prompts = _normalize_callback_prompts(
                _callback_arg(args, kwargs, 0, "prompts", [])
            )
            value = interpreter.llm_query_batched(prompts)
        elif name == "sub_rlm":
            prompt = _callback_arg(args, kwargs, 0, "prompt", "")
            context = _callback_arg(args, kwargs, 1, "context", "")
            value = interpreter.sub_rlm(str(prompt), context=str(context or ""))
        elif name == "sub_rlm_batched":
            prompts = _normalize_callback_prompts(
                _callback_arg(args, kwargs, 0, "prompts", [])
            )
            context = _callback_arg(args, kwargs, 1, "context", "")
            value = interpreter.sub_rlm_batched(prompts, context=str(context or ""))
        elif name in interpreter._tools:
            value = interpreter._tools[name](*args, **kwargs)
        elif name == "fetch_document_text":
            from fleet_rlm.runtime.tools.document_tools import fetch_document_text

            value = fetch_document_text(*args, **kwargs)
        else:
            raise RuntimeError(f"Unknown host callback: {name}")
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _callback_arg(
    args: list[Any],
    kwargs: dict[str, Any],
    index: int,
    name: str,
    default: Any,
) -> Any:
    if len(args) > index:
        return args[index]
    return kwargs.get(name, default)


def _normalize_callback_prompts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


__all__ = [
    "bridge_tools",
    "invoke_tool",
    "reject_unsupported_recursive_callbacks",
    "requires_bridge",
]
