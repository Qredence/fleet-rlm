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
    r"(?<![\w.-])(?:(?:workspace|projects)/)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,16}(?![\w.-])"
)

# Host tool name -> stable guard-target namespace. Fingerprints for existing
# ``session_workspace:`` targets are unchanged; ``projects/<slug>/<path>``
# targets join as ``project_workspace:<slug>/<path>``. The delete/edit tools
# (WS-7) track against the same targets.
_WORKSPACE_TOOL_NAMESPACES = {
    "write_workspace_text": "session_workspace",
    "append_workspace_text": "session_workspace",
    "read_workspace_text": "session_workspace",
    "delete_workspace_path": "session_workspace",
    "edit_workspace_text": "session_workspace",
    "write_project_text": "project_workspace",
    "read_project_text": "project_workspace",
    "delete_project_path": "project_workspace",
    "edit_project_text": "project_workspace",
}

_PREFIX_NAMESPACES = (("projects/", "project_workspace"), ("workspace/", "session_workspace"))


def _canonical_target(path: object, *, namespace: str | None = None) -> str | None:
    """Canonicalize one guard target; an explicit tool namespace is authoritative.

    Without ``namespace`` (request-text obligations) the guard-target language
    infers the namespace from a ``projects/`` or ``workspace/`` path prefix.
    With ``namespace`` (tool-derived targets) prefixes never cross namespaces:
    project tools tolerate only a redundant leading ``projects/`` segment
    (mirroring ``_normalize_project_path``), and session-workspace tools use
    their paths verbatim, so a ``projects/...`` path passed to a session tool
    stays a ``session_workspace:`` target.
    """
    if not isinstance(path, str):
        return None
    try:
        from fleet_rlm.files.workspace_validation import WorkspacePathError, normalize_workspace_path

        normalized = normalize_workspace_path(path)
    except (TypeError, WorkspacePathError):
        return None
    if namespace is None:
        namespace = "session_workspace"
        for prefix, prefix_namespace in _PREFIX_NAMESPACES:
            if normalized.startswith(prefix):
                namespace = prefix_namespace
                normalized = normalized.removeprefix(prefix)
                break
    elif namespace == "project_workspace":
        normalized = normalized.removeprefix("projects/")
    return f"{namespace}:{normalized}"


def _workspace_target(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
    namespace = _WORKSPACE_TOOL_NAMESPACES.get(tool_name)
    if namespace is None:
        return None
    return _canonical_target(arguments.get("path"), namespace=namespace)


def workspace_obligations(request: str) -> frozenset[str] | None:
    """Extract explicit workspace and project file targets from the user's task text."""
    targets: set[str] = set()
    for match in _WORKSPACE_PATH_RE.finditer(request):
        target = _canonical_target(match.group(0))
        if target:
            targets.add(target)
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
        if tool_name in {"write_workspace_text", "write_project_text"}:
            content = arguments.get("content")
            if isinstance(content, str) and target in self._unresolved:
                self._expected_content[target] = sha256(content.encode("utf-8")).hexdigest()
            return
        if tool_name == "append_workspace_text":
            self._unresolved.discard(target)
            self._expected_content.pop(target, None)
            return
        if tool_name in {
            "delete_workspace_path",
            "delete_project_path",
            "edit_workspace_text",
            "edit_project_text",
        }:
            # A successful delete/edit settles the obligation: the mutation is
            # atomic and immediately durable, and its receipt is the completion
            # (a deleted path cannot be read back; an edit's full content is
            # not derivable from its old/new fragments).
            self._unresolved.discard(target)
            self._expected_content.pop(target, None)
            return
        if tool_name in {"read_workspace_text", "read_project_text"}:
            content: object = result
            eof = True
            if isinstance(result, Mapping):
                content = result.get("content")
                eof = result.get("eof") is not False
            expected = self._expected_content.get(target)
            if eof and isinstance(content, str) and expected == sha256(content.encode("utf-8")).hexdigest():
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
