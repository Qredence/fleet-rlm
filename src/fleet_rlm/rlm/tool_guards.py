"""Per-Turn host-tool integrity and forward-progress safeguards."""

from __future__ import annotations

import re
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


_WORKSPACE_PATH_RE = re.compile(
    r"(?<![\w.-])(?:workspace/)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,16}(?![\w.-])"
)


def _canonical_path(path: object) -> str | None:
    if not isinstance(path, str):
        return None
    try:
        from fleet_rlm.files.workspace_validation import WorkspacePathError, normalize_workspace_path

        normalized = normalize_workspace_path(path)
    except (TypeError, WorkspacePathError):
        return None
    if normalized.startswith("workspace/"):
        normalized = normalized.removeprefix("workspace/")
    return normalized


def _workspace_target(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
    if tool_name not in {"write_workspace_text", "read_workspace_text"}:
        return None
    path = _canonical_path(arguments.get("path"))
    return f"session_workspace:{path}" if path else None


def workspace_obligations(request: str) -> frozenset[str] | None:
    """Extract explicit workspace file targets from the user's task text."""
    targets: set[str] = set()
    for match in _WORKSPACE_PATH_RE.finditer(request):
        path = _canonical_path(match.group(0))
        if path:
            targets.add(f"session_workspace:{path}")
    return frozenset(targets) if targets else None


@dataclass(slots=True)
class TurnIntegrityLedger:
    """Keep failed required workspace mutations unresolved until repaired in-place."""

    _unresolved: set[str] = field(default_factory=set)
    required_targets: frozenset[str] | None = None
    _expected_content: dict[str, str] = field(default_factory=dict)

    def _target(self, tool_name: str, arguments: Mapping[str, Any]) -> str | None:
        target = _workspace_target(tool_name, arguments)
        if target is None:
            return None
        if self.required_targets is not None and target not in self.required_targets:
            return None
        return target

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if target := self._target(tool_name, arguments):
            self._unresolved.add(target)

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> None:
        target = self._target(tool_name, arguments)
        if target is None:
            return
        if tool_name == "write_workspace_text":
            content = arguments.get("content")
            if isinstance(content, str) and target in self._unresolved:
                self._expected_content[target] = sha256(content.encode("utf-8")).hexdigest()
            return
        if tool_name == "read_workspace_text" and isinstance(result, str):
            expected = self._expected_content.get(target)
            if expected == sha256(result.encode("utf-8")).hexdigest():
                self._unresolved.discard(target)
                self._expected_content.pop(target, None)

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
    required_targets: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.required_targets is not None:
            self.integrity.required_targets = self.required_targets

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> str | None:
        self.integrity.completed(tool_name, arguments, result)
        return self.progress.completed(tool_name, arguments, result)

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        self.integrity.failed(tool_name, arguments)
