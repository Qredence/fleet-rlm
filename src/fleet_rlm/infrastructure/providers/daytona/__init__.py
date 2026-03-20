"""Daytona provider surface with lazy exports to avoid heavy import cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "DaytonaWorkbenchChatAgent": (".chat_agent", "DaytonaWorkbenchChatAgent"),
    "DaytonaConfigError": (".config", "DaytonaConfigError"),
    "ResolvedDaytonaConfig": (".config", "ResolvedDaytonaConfig"),
    "resolve_daytona_config": (".config", "resolve_daytona_config"),
    "DaytonaDiagnosticError": (".diagnostics", "DaytonaDiagnosticError"),
    "DaytonaInterpreter": (".interpreter", "DaytonaInterpreter"),
    "DaytonaSandboxRuntime": (".sandbox", "DaytonaSandboxRuntime"),
    "DaytonaSandboxSession": (".sandbox", "DaytonaSandboxSession"),
    "run_daytona_smoke": (".smoke", "run_daytona_smoke"),
    "AgentNode": (".types", "AgentNode"),
    "ChildLink": (".types", "ChildLink"),
    "ChildTaskResult": (".types", "ChildTaskResult"),
    "ContextSource": (".types", "ContextSource"),
    "DaytonaEvidenceRef": (".types", "DaytonaEvidenceRef"),
    "DaytonaRunResult": (".types", "DaytonaRunResult"),
    "DaytonaSmokeResult": (".types", "DaytonaSmokeResult"),
    "ExecutionObservation": (".types", "ExecutionObservation"),
    "FinalArtifact": (".types", "FinalArtifact"),
    "PromptHandle": (".types", "PromptHandle"),
    "PromptManifest": (".types", "PromptManifest"),
    "PromptSliceRef": (".types", "PromptSliceRef"),
    "RecursiveTaskSpec": (".types", "RecursiveTaskSpec"),
    "RolloutBudget": (".types", "RolloutBudget"),
    "RolloutSummary": (".types", "RolloutSummary"),
    "TaskSourceProvenance": (".types", "TaskSourceProvenance"),
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
