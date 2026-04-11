"""DSPy agent/program surface with lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AssembleRecursiveWorkspaceContext": (
        ".signatures",
        "AssembleRecursiveWorkspaceContext",
    ),
    "AssembleRecursiveWorkspaceContextModule": (
        ".recursive_context_selection",
        "AssembleRecursiveWorkspaceContextModule",
    ),
    "PlanRecursiveSubqueries": (
        ".signatures",
        "PlanRecursiveSubqueries",
    ),
    "PlanRecursiveSubqueriesModule": (
        ".recursive_decomposition",
        "PlanRecursiveSubqueriesModule",
    ),
    "RLMReActChatAgent": (".chat_agent", "RLMReActChatAgent"),
    "ReflectAndReviseWorkspaceStepModule": (
        ".recursive_reflection",
        "ReflectAndReviseWorkspaceStepModule",
    ),
    "RLMReActChatSignature": (".signatures", "RLMReActChatSignature"),
    "ReflectAndReviseWorkspaceStep": (
        ".signatures",
        "ReflectAndReviseWorkspaceStep",
    ),
    "RecursiveSubQuerySignature": (".signatures", "RecursiveSubQuerySignature"),
    "spawn_delegate_sub_agent_async": (
        ".recursive_runtime",
        "spawn_delegate_sub_agent_async",
    ),
    "COMMAND_DISPATCH": (".commands", "COMMAND_DISPATCH"),
    "execute_command": (".commands", "execute_command"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:  # pragma: no cover - Python import protocol
        raise AttributeError(name) from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
