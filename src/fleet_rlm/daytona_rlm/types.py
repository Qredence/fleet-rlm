"""Core types for the experimental Daytona-backed RLM pilot."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


class DaytonaRunCancelled(RuntimeError):
    """Raised when a live Daytona rollout is cancelled by the caller."""


@dataclass(slots=True)
class RolloutBudget:
    """Global budget for one Daytona RLM rollout."""

    max_sandboxes: int = 50
    max_depth: int = 2
    max_iterations: int = 50
    global_timeout: int = 3600
    result_truncation_limit: int = 10_000
    batch_concurrency: int = 4


_WHITESPACE_RE = re.compile(r"\s+")
_PROMPT_PREVIEW_LIMIT = 240
_PERSISTED_TEXT_LIMIT = 1_200


def _normalize_optional_text(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    if limit is not None and len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _persisted_text_preview(value: str, *, limit: int = _PERSISTED_TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n[truncated persisted preview]"


@dataclass(slots=True)
class PromptHandle:
    """Metadata for one externalized prompt object stored in the sandbox."""

    handle_id: str
    kind: str
    label: str | None = None
    path: str = ""
    char_count: int = 0
    line_count: int = 0
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> "PromptHandle":
        if not isinstance(raw, dict):
            raise ValueError("Prompt handle payload must be a dict.")
        handle_id = _normalize_optional_text(raw.get("handle_id"))
        if handle_id is None:
            raise ValueError("Prompt handle is missing handle_id.")
        kind = _normalize_optional_text(raw.get("kind")) or "manual"
        return cls(
            handle_id=handle_id,
            kind=kind,
            label=_normalize_optional_text(raw.get("label")),
            path=str(raw.get("path", "") or ""),
            char_count=_coerce_nonnegative_int(raw.get("char_count")) or 0,
            line_count=_coerce_nonnegative_int(raw.get("line_count")) or 0,
            preview=_normalize_optional_text(
                raw.get("preview"), limit=_PROMPT_PREVIEW_LIMIT
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromptSliceRef:
    """Metadata describing one bounded prompt slice read from the sandbox."""

    handle_id: str
    start_line: int | None = None
    end_line: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> "PromptSliceRef":
        if not isinstance(raw, dict):
            raise ValueError("Prompt slice payload must be a dict.")
        handle_id = _normalize_optional_text(raw.get("handle_id"))
        if handle_id is None:
            raise ValueError("Prompt slice is missing handle_id.")
        return cls(
            handle_id=handle_id,
            start_line=_coerce_positive_int(raw.get("start_line")),
            end_line=_coerce_positive_int(raw.get("end_line")),
            start_char=_coerce_nonnegative_int(raw.get("start_char")),
            end_char=_coerce_nonnegative_int(raw.get("end_char")),
            preview=_normalize_optional_text(
                raw.get("preview"), limit=_PROMPT_PREVIEW_LIMIT
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromptManifest:
    """Collection of prompt handles currently available in one sandbox."""

    handles: list[PromptHandle] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> "PromptManifest":
        if not isinstance(raw, dict):
            return cls()
        handles_raw = raw.get("handles", [])
        if not isinstance(handles_raw, list):
            return cls()
        handles: list[PromptHandle] = []
        for item in handles_raw:
            try:
                handles.append(PromptHandle.from_raw(item))
            except ValueError:
                continue
        return cls(handles=handles)

    def to_dict(self) -> dict[str, Any]:
        return {"handles": [handle.to_dict() for handle in self.handles]}


@dataclass(slots=True)
class TaskSourceProvenance:
    """Normalized provenance for one recursive child task."""

    kind: str
    source_id: str | None = None
    path: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    chunk_index: int | None = None
    header: str | None = None
    pattern: str | None = None
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> "TaskSourceProvenance":
        if not isinstance(raw, dict):
            return cls(kind="manual")

        kind = _normalize_optional_text(raw.get("kind")) or "manual"
        line = _coerce_positive_int(raw.get("line"))
        start_line = _coerce_positive_int(raw.get("start_line"))
        end_line = _coerce_positive_int(raw.get("end_line"))
        chunk_index = _coerce_nonnegative_int(raw.get("chunk_index"))

        if line is not None:
            start_line = start_line or line
            end_line = end_line or line
        if start_line is not None and end_line is None:
            end_line = start_line
        if start_line is not None and end_line is not None and end_line < start_line:
            end_line = start_line

        normalized = cls(
            kind=kind,
            source_id=_normalize_optional_text(raw.get("source_id")),
            path=_normalize_optional_text(raw.get("path")),
            line=line,
            start_line=start_line,
            end_line=end_line,
            chunk_index=chunk_index,
            header=_normalize_optional_text(raw.get("header")),
            pattern=_normalize_optional_text(raw.get("pattern")),
            preview=_normalize_optional_text(raw.get("preview"), limit=240),
        )
        if normalized.source_id is None:
            normalized.source_id = normalized._derive_source_id()
        return normalized

    def _derive_source_id(self) -> str | None:
        stable_parts = [
            self.kind,
            self.path or "",
            str(self.start_line or ""),
            str(self.end_line or ""),
            str(self.chunk_index if self.chunk_index is not None else ""),
            self.header or "",
            self.pattern or "",
        ]
        if not any(stable_parts[1:]):
            return None
        digest = hashlib.sha1("|".join(stable_parts).encode("utf-8")).hexdigest()[:16]
        return f"src-{digest}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RecursiveTaskSpec:
    """Normalized recursive child task spec."""

    task: str
    label: str | None = None
    source: TaskSourceProvenance = field(
        default_factory=lambda: TaskSourceProvenance(kind="manual")
    )

    @classmethod
    def from_raw(cls, raw: Any) -> "RecursiveTaskSpec":
        if isinstance(raw, str):
            task = _normalize_optional_text(raw)
            if task is None:
                raise ValueError("Recursive task cannot be empty.")
            return cls(task=task)

        if isinstance(raw, dict):
            task = _normalize_optional_text(raw.get("task"))
            if task is None:
                raise ValueError("Recursive task cannot be empty.")
            label = _normalize_optional_text(raw.get("label"))
            source = TaskSourceProvenance.from_raw(raw.get("source"))
            return cls(task=task, label=label, source=source)

        raise ValueError(f"Unsupported recursive task payload: {type(raw).__name__}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "label": self.label,
            "source": self.source.to_dict(),
        }


@dataclass(slots=True)
class ChildLink:
    """Persisted parent -> child recursive linkage."""

    child_id: str | None
    callback_name: str
    task: RecursiveTaskSpec
    result_preview: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "callback_name": self.callback_name,
            "task": self.task.to_dict(),
            "result_preview": self.result_preview,
            "status": self.status,
        }


@dataclass(slots=True)
class ChildTaskResult:
    """Internal child execution result returned to recursive callers."""

    child_id: str | None
    task: RecursiveTaskSpec
    text: str
    result_preview: str
    status: str


@dataclass(slots=True)
class FinalArtifact:
    """Structured final artifact produced by a node."""

    kind: str
    value: Any
    variable_name: str | None = None
    finalization_mode: str = "fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionObservation:
    """Bounded execution result for a single iteration."""

    iteration: int
    code: str
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: int = 0
    callback_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentNode:
    """Serialized execution state for one root or child node."""

    node_id: str
    parent_id: str | None
    depth: int
    task: str
    repo: str
    ref: str | None
    sandbox_id: str | None = None
    workspace_path: str | None = None
    status: str = "running"
    prompt_handles: list[PromptHandle] = field(default_factory=list)
    prompt_previews: list[str] = field(default_factory=list)
    response_previews: list[str] = field(default_factory=list)
    observations: list[ExecutionObservation] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)
    child_links: list[ChildLink] = field(default_factory=list)
    final_artifact: FinalArtifact | None = None
    iteration_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task"] = _persisted_text_preview(self.task)
        payload["prompt_handles"] = [item.to_dict() for item in self.prompt_handles]
        payload["observations"] = [item.to_dict() for item in self.observations]
        payload["child_links"] = [item.to_dict() for item in self.child_links]
        payload["final_artifact"] = (
            self.final_artifact.to_dict() if self.final_artifact is not None else None
        )
        return payload


@dataclass(slots=True)
class RolloutSummary:
    """Top-level summary for one Daytona RLM rollout."""

    duration_ms: int
    sandboxes_used: int
    termination_reason: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DaytonaRunResult:
    """Top-level rollout result persisted to disk."""

    run_id: str
    repo: str
    ref: str | None
    task: str
    budget: RolloutBudget
    root_id: str
    nodes: dict[str, AgentNode]
    final_artifact: FinalArtifact | None
    summary: RolloutSummary
    result_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo": self.repo,
            "ref": self.ref,
            "task": _persisted_text_preview(self.task),
            "budget": asdict(self.budget),
            "root_id": self.root_id,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "final_artifact": (
                self.final_artifact.to_dict()
                if self.final_artifact is not None
                else None
            ),
            "summary": self.summary.to_dict(),
            "result_path": self.result_path,
        }


@dataclass(slots=True)
class DaytonaSmokeResult:
    """Result of a Daytona live/runtime smoke check."""

    repo: str
    ref: str | None
    sandbox_id: str | None
    repo_path: str = ""
    persisted_state_value: Any = None
    driver_started: bool = False
    finalization_mode: str = "unknown"
    termination_phase: str = "config"
    error_category: str | None = None
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
