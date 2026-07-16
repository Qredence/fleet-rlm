"""Observe synchronous host tools without changing native DSPy internals."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import Any
from uuid import uuid4

import dspy

from fleet_rlm.rlm.events import ObservationObserver, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.sanitize import sanitize_public_error, sanitize_public_value
from fleet_rlm.skills.capabilities import RLMTool

_PROTECTED_TOOL_NAMES = frozenset(
    {
        "read_attachment",
        "create_artifact",
        "load_skill",
        "read_skill_resource",
        "read_session_history",
        "list_workspace_files",
        "stat_workspace_file",
        "read_workspace_text",
        "write_workspace_text",
        "llm_query",
        "llm_query_batched",
    }
)


@dataclass(frozen=True, slots=True)
class ToolEventView:
    """Bounded public projection policy for observed tool calls."""

    max_chars: int = 2_000

    def input(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        return _public_tool_input(name, args, kwargs, max_chars=max(4, int(self.max_chars)))

    def output(self, name: str, result: Any) -> Any:
        return _public_tool_output(name, result, max_chars=max(4, int(self.max_chars)))

    def error(self, name: str, exc: BaseException) -> str:
        if name in _PROTECTED_TOOL_NAMES:
            return "Protected tool failed"
        return sanitize_public_error(exc)


def _argument(args: tuple[Any, ...], kwargs: dict[str, Any], name: str, index: int) -> Any:
    return kwargs.get(name, args[index] if len(args) > index else None)


def _safe_value(value: Any, *, max_chars: int) -> Any:
    return sanitize_public_value(value, max_len=max_chars)


def _public_tool_input(
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    max_chars: int,
) -> Any:
    if name == "read_attachment":
        return {"attachment_id": _safe_value(_argument(args, kwargs, "attachment_id", 0), max_chars=max_chars)}
    if name == "create_artifact":
        content = _argument(args, kwargs, "content", 1)
        return {
            "kind": _safe_value(_argument(args, kwargs, "kind", 0), max_chars=max_chars),
            "title": _safe_value(_argument(args, kwargs, "title", 2), max_chars=max_chars),
            "content_chars": len(str(content or "")),
        }
    if name == "load_skill":
        return {"skill_id": _safe_value(_argument(args, kwargs, "skill_id", 0), max_chars=max_chars)}
    if name == "read_skill_resource":
        return {
            "skill_id": _safe_value(_argument(args, kwargs, "skill_id", 0), max_chars=max_chars),
            "resource_id": _safe_value(_argument(args, kwargs, "resource_id", 1), max_chars=max_chars),
        }
    if name == "read_session_history":
        return {
            "offset": _safe_value(_argument(args, kwargs, "offset", 0), max_chars=max_chars),
            "limit": _safe_value(_argument(args, kwargs, "limit", 1), max_chars=max_chars),
        }
    if name == "list_workspace_files":
        return {
            "path": _safe_value(_argument(args, kwargs, "path", 0), max_chars=max_chars),
            "limit": _safe_value(_argument(args, kwargs, "limit", 1), max_chars=max_chars),
        }
    if name == "stat_workspace_file":
        return {"path": _safe_value(_argument(args, kwargs, "path", 0), max_chars=max_chars)}
    if name == "read_workspace_text":
        return {
            "path": _safe_value(_argument(args, kwargs, "path", 0), max_chars=max_chars),
            "max_chars": _safe_value(_argument(args, kwargs, "max_chars", 1), max_chars=max_chars),
        }
    if name == "write_workspace_text":
        content = _argument(args, kwargs, "content", 1)
        return {
            "path": _safe_value(_argument(args, kwargs, "path", 0), max_chars=max_chars),
            "overwrite": _safe_value(_argument(args, kwargs, "overwrite", 2), max_chars=max_chars),
            "content_chars": len(str(content or "")),
        }
    if name == "llm_query":
        prompt = _argument(args, kwargs, "prompt", 0)
        return {"prompt_chars": len(str(prompt or ""))}
    if name == "llm_query_batched":
        prompts = _argument(args, kwargs, "prompts", 0)
        values = list(prompts) if isinstance(prompts, (list, tuple)) else []
        return {"prompt_count": len(values), "prompt_chars": sum(len(str(value)) for value in values)}
    return {
        "args": _safe_value(args, max_chars=max_chars),
        "kwargs": _safe_value(kwargs, max_chars=max_chars),
    }


def _public_tool_output(name: str, result: Any, *, max_chars: int) -> Any:
    if not isinstance(result, dict):
        return _safe_value(result, max_chars=max_chars)
    if name == "read_attachment":
        allowed = ("ok", "attachment_id", "filename", "content_type", "encoding")
    elif name == "create_artifact":
        allowed = ("ok", "kind", "title", "byte_size")
    elif name == "load_skill":
        allowed = ("ok", "skill_id", "name", "version", "trust")
    elif name == "read_skill_resource":
        allowed = ("ok", "skill_id", "resource_id", "byte_size")
    elif name == "read_session_history":
        messages = result.get("messages")
        projected = {
            key: _safe_value(result[key], max_chars=max_chars)
            for key in ("offset", "next_offset", "total")
            if key in result
        }
        projected["message_count"] = len(messages) if isinstance(messages, list) else 0
        return projected
    elif name == "list_workspace_files":
        allowed = ("ok", "error", "path", "count")
    elif name == "stat_workspace_file":
        projected = {key: _safe_value(result[key], max_chars=max_chars) for key in ("ok", "error") if key in result}
        entry = result.get("entry")
        if isinstance(entry, dict):
            for key in ("path", "byte_size"):
                if key in entry:
                    projected[key] = _safe_value(entry[key], max_chars=max_chars)
        return projected
    elif name == "read_workspace_text":
        allowed = ("ok", "error", "path", "chars", "byte_size")
    elif name == "write_workspace_text":
        allowed = ("ok", "error", "path", "byte_size")
    else:
        return _safe_value(result, max_chars=max_chars)
    return {key: _safe_value(result[key], max_chars=max_chars) for key in allowed if key in result}


def observe_tool(
    tool: RLMTool,
    observer: ObservationObserver,
    event_view: ToolEventView,
) -> dspy.Tool:
    """Return a fresh DSPy tool that publishes bounded start/completion/failure details."""
    source = tool if isinstance(tool, dspy.Tool) else dspy.Tool(tool)

    @wraps(source.func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        call_id = str(uuid4())
        observer(ToolStarted(call_id, str(source.name), event_view.input(str(source.name), args, kwargs)))
        try:
            result = source.func(*args, **kwargs)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("async host tools are not supported inside the synchronous interpreter bridge")
        except Exception as exc:
            observer(ToolFailed(call_id, str(source.name), event_view.error(str(source.name), exc)))
            raise
        observer(ToolCompleted(call_id, str(source.name), event_view.output(str(source.name), result)))
        return result

    return dspy.Tool(
        wrapped,
        name=source.name,
        desc=source.desc,
        args=source.args,
        arg_types=source.arg_types,
        arg_desc=source.arg_desc,
    )
