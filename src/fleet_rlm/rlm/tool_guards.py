"""Per-Turn host-tool integrity and forward-progress safeguards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps
from typing import Any


def _fingerprint(tool_name: str, arguments: Mapping[str, Any], result: object) -> str:
    """Fingerprint private values without retaining their bodies in the guard."""
    value = dumps(
        {"tool": tool_name, "arguments": arguments, "result": result},
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(value.encode("utf-8")).hexdigest()


def _workspace_target(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
    if tool_name != "write_workspace_text":
        return None
    path = arguments.get("path")
    return f"session_workspace:{path}" if isinstance(path, str) else None


@dataclass(slots=True)
class TurnIntegrityLedger:
    """Keep failed required workspace mutations unresolved until repaired in-place."""

    _unresolved: set[str] = field(default_factory=set)

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if target := _workspace_target(tool_name, arguments):
            self._unresolved.add(target)

    def completed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if target := _workspace_target(tool_name, arguments):
            self._unresolved.discard(target)

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(sorted(self._unresolved))


@dataclass(slots=True)
class ToolProgressGuard:
    """Emit one bounded warning for identical consecutive host-tool calls."""

    _previous: str | None = None
    _repetitions: int = 0

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> str | None:
        fingerprint = _fingerprint(tool_name, arguments, result)
        if fingerprint == self._previous:
            self._repetitions += 1
        else:
            self._previous = fingerprint
            self._repetitions = 0
        if self._repetitions == 1:
            return "repeated tool call produced no progress"
        return None


@dataclass(slots=True)
class TurnToolGuards:
    """Small runner-facing interface consolidating mutable per-Turn safeguards."""

    integrity: TurnIntegrityLedger = field(default_factory=TurnIntegrityLedger)
    progress: ToolProgressGuard = field(default_factory=ToolProgressGuard)

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> str | None:
        self.integrity.completed(tool_name, arguments)
        return self.progress.completed(tool_name, arguments, result)

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        self.integrity.failed(tool_name, arguments)
