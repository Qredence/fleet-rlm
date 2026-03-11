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

    @classmethod
    def from_raw(cls, raw: Any) -> "RolloutBudget":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            max_sandboxes=_coerce_positive_int(raw.get("max_sandboxes")) or 50,
            max_depth=_coerce_nonnegative_int(raw.get("max_depth")) or 2,
            max_iterations=_coerce_positive_int(raw.get("max_iterations")) or 50,
            global_timeout=_coerce_positive_int(raw.get("global_timeout")) or 3600,
            result_truncation_limit=(
                _coerce_positive_int(raw.get("result_truncation_limit")) or 10_000
            ),
            batch_concurrency=_coerce_positive_int(raw.get("batch_concurrency")) or 4,
        )


@dataclass(slots=True)
class SandboxLmRuntimeConfig:
    """Serializable LM bootstrap config passed into sandbox-local runtimes."""

    model: str
    api_key: str
    api_base: str | None = None
    max_tokens: int = 64_000
    delegate_model: str | None = None
    delegate_api_key: str | None = None
    delegate_api_base: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> "SandboxLmRuntimeConfig":
        if not isinstance(raw, dict):
            raise ValueError("Sandbox LM config must be a dict.")
        model = _normalize_optional_text(raw.get("model"))
        api_key = _normalize_optional_text(raw.get("api_key"))
        if model is None or api_key is None:
            raise ValueError("Sandbox LM config requires model and api_key.")
        return cls(
            model=model,
            api_key=api_key,
            api_base=_normalize_optional_text(raw.get("api_base")),
            max_tokens=_coerce_positive_int(raw.get("max_tokens")) or 64_000,
            delegate_model=_normalize_optional_text(raw.get("delegate_model")),
            delegate_api_key=_normalize_optional_text(raw.get("delegate_api_key")),
            delegate_api_base=_normalize_optional_text(raw.get("delegate_api_base")),
        )


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
class ContextSource:
    """Host-sourced local context staged into the Daytona workspace."""

    source_id: str
    kind: str
    host_path: str
    staged_path: str
    source_type: str | None = None
    extraction_method: str | None = None
    file_count: int = 1
    skipped_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> "ContextSource":
        if not isinstance(raw, dict):
            raise ValueError("Context source payload must be a dict.")
        source_id = _normalize_optional_text(raw.get("source_id"))
        kind = _normalize_optional_text(raw.get("kind"))
        host_path = _normalize_optional_text(raw.get("host_path"))
        staged_path = _normalize_optional_text(raw.get("staged_path"))
        if (
            source_id is None
            or kind is None
            or host_path is None
            or staged_path is None
        ):
            raise ValueError(
                "Context source requires source_id, kind, host_path, and staged_path."
            )
        return cls(
            source_id=source_id,
            kind=kind,
            host_path=host_path,
            staged_path=staged_path,
            source_type=_normalize_optional_text(raw.get("source_type")),
            extraction_method=_normalize_optional_text(raw.get("extraction_method")),
            file_count=_coerce_positive_int(raw.get("file_count")) or 1,
            skipped_count=_coerce_nonnegative_int(raw.get("skipped_count")) or 0,
            warnings=[
                str(item)
                for item in raw.get("warnings", []) or []
                if item is not None and str(item).strip()
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        digest = hashlib.sha1(
            "|".join(stable_parts).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]
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

    @classmethod
    def from_raw(cls, raw: Any) -> "ChildLink":
        if not isinstance(raw, dict):
            raise ValueError("Child link payload must be a dict.")
        return cls(
            child_id=_normalize_optional_text(raw.get("child_id")),
            callback_name=_normalize_optional_text(raw.get("callback_name"))
            or "llm_query",
            task=RecursiveTaskSpec.from_raw(raw.get("task", {})),
            result_preview=_normalize_optional_text(
                raw.get("result_preview"), limit=280
            )
            or "",
            status=_normalize_optional_text(raw.get("status")) or "completed",
        )


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

    @classmethod
    def from_raw(cls, raw: Any) -> "FinalArtifact":
        if not isinstance(raw, dict):
            raise ValueError("Final artifact payload must be a dict.")
        return cls(
            kind=_normalize_optional_text(raw.get("kind")) or "markdown",
            value=raw.get("value"),
            variable_name=_normalize_optional_text(raw.get("variable_name")),
            finalization_mode=(
                _normalize_optional_text(raw.get("finalization_mode")) or "fallback"
            ),
        )


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

    @classmethod
    def from_raw(cls, raw: Any) -> "ExecutionObservation":
        if not isinstance(raw, dict):
            raise ValueError("Execution observation payload must be a dict.")
        return cls(
            iteration=_coerce_positive_int(raw.get("iteration")) or 1,
            code=str(raw.get("code", "") or ""),
            stdout=str(raw.get("stdout", "") or ""),
            stderr=str(raw.get("stderr", "") or ""),
            error=_normalize_optional_text(raw.get("error")),
            duration_ms=_coerce_nonnegative_int(raw.get("duration_ms")) or 0,
            callback_count=_coerce_nonnegative_int(raw.get("callback_count")) or 0,
        )


@dataclass(slots=True)
class AgentNode:
    """Serialized execution state for one root or child node."""

    node_id: str
    parent_id: str | None
    depth: int
    task: str
    repo: str
    ref: str | None
    context_sources: list[ContextSource] = field(default_factory=list)
    sandbox_id: str | None = None
    workspace_path: str | None = None
    status: str = "running"
    prompt_handles: list[PromptHandle] = field(default_factory=list)
    prompt_previews: list[str] = field(default_factory=list)
    response_previews: list[str] = field(default_factory=list)
    observations: list[ExecutionObservation] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)
    child_links: list[ChildLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final_artifact: FinalArtifact | None = None
    iteration_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task"] = _persisted_text_preview(self.task)
        payload["context_sources"] = [item.to_dict() for item in self.context_sources]
        payload["prompt_handles"] = [item.to_dict() for item in self.prompt_handles]
        payload["observations"] = [item.to_dict() for item in self.observations]
        payload["child_links"] = [item.to_dict() for item in self.child_links]
        payload["warnings"] = list(self.warnings)
        payload["final_artifact"] = (
            self.final_artifact.to_dict() if self.final_artifact is not None else None
        )
        return payload

    @classmethod
    def from_raw(cls, raw: Any) -> "AgentNode":
        if not isinstance(raw, dict):
            raise ValueError("Agent node payload must be a dict.")
        return cls(
            node_id=_normalize_optional_text(raw.get("node_id")) or "",
            parent_id=_normalize_optional_text(raw.get("parent_id")),
            depth=_coerce_nonnegative_int(raw.get("depth")) or 0,
            task=str(raw.get("task", "") or ""),
            repo=str(raw.get("repo", "") or ""),
            ref=_normalize_optional_text(raw.get("ref")),
            context_sources=[
                ContextSource.from_raw(item)
                for item in raw.get("context_sources", []) or []
                if isinstance(item, dict)
            ],
            sandbox_id=_normalize_optional_text(raw.get("sandbox_id")),
            workspace_path=_normalize_optional_text(raw.get("workspace_path")),
            status=_normalize_optional_text(raw.get("status")) or "running",
            prompt_handles=[
                PromptHandle.from_raw(item)
                for item in raw.get("prompt_handles", []) or []
                if isinstance(item, dict)
            ],
            prompt_previews=[
                str(item)
                for item in raw.get("prompt_previews", []) or []
                if isinstance(item, str)
            ],
            response_previews=[
                str(item)
                for item in raw.get("response_previews", []) or []
                if isinstance(item, str)
            ],
            observations=[
                ExecutionObservation.from_raw(item)
                for item in raw.get("observations", []) or []
                if isinstance(item, dict)
            ],
            child_ids=[
                str(item) for item in raw.get("child_ids", []) or [] if item is not None
            ],
            child_links=[
                ChildLink.from_raw(item)
                for item in raw.get("child_links", []) or []
                if isinstance(item, dict)
            ],
            warnings=[
                str(item) for item in raw.get("warnings", []) or [] if item is not None
            ],
            final_artifact=(
                FinalArtifact.from_raw(raw.get("final_artifact"))
                if isinstance(raw.get("final_artifact"), dict)
                else None
            ),
            iteration_count=_coerce_nonnegative_int(raw.get("iteration_count")) or 0,
            error=_normalize_optional_text(raw.get("error")),
        )


@dataclass(slots=True)
class RolloutSummary:
    """Top-level summary for one Daytona RLM rollout."""

    duration_ms: int
    sandboxes_used: int
    termination_reason: str
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> "RolloutSummary":
        if not isinstance(raw, dict):
            raise ValueError("Rollout summary payload must be a dict.")
        return cls(
            duration_ms=_coerce_nonnegative_int(raw.get("duration_ms")) or 0,
            sandboxes_used=_coerce_nonnegative_int(raw.get("sandboxes_used")) or 0,
            termination_reason=(
                _normalize_optional_text(raw.get("termination_reason")) or "completed"
            ),
            error=_normalize_optional_text(raw.get("error")),
            warnings=[
                str(item) for item in raw.get("warnings", []) or [] if item is not None
            ],
        )


@dataclass(slots=True)
class DaytonaRunResult:
    """Top-level rollout result persisted to disk."""

    run_id: str
    repo: str
    ref: str | None
    context_sources: list[ContextSource]
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
            "context_sources": [item.to_dict() for item in self.context_sources],
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

    @classmethod
    def from_raw(cls, raw: Any) -> "DaytonaRunResult":
        if not isinstance(raw, dict):
            raise ValueError("Daytona run result payload must be a dict.")
        budget_raw = raw.get("budget")
        if not isinstance(budget_raw, dict):
            raise ValueError("Daytona run result requires a budget dict.")
        nodes_raw = raw.get("nodes")
        if not isinstance(nodes_raw, dict):
            raise ValueError("Daytona run result requires nodes.")
        return cls(
            run_id=_normalize_optional_text(raw.get("run_id")) or "",
            repo=str(raw.get("repo", "") or ""),
            ref=_normalize_optional_text(raw.get("ref")),
            context_sources=[
                ContextSource.from_raw(item)
                for item in raw.get("context_sources", []) or []
                if isinstance(item, dict)
            ],
            task=str(raw.get("task", "") or ""),
            budget=RolloutBudget(
                max_sandboxes=_coerce_positive_int(budget_raw.get("max_sandboxes"))
                or 50,
                max_depth=_coerce_nonnegative_int(budget_raw.get("max_depth")) or 2,
                max_iterations=_coerce_positive_int(budget_raw.get("max_iterations"))
                or 50,
                global_timeout=_coerce_positive_int(budget_raw.get("global_timeout"))
                or 3600,
                result_truncation_limit=_coerce_positive_int(
                    budget_raw.get("result_truncation_limit")
                )
                or 10_000,
                batch_concurrency=_coerce_positive_int(
                    budget_raw.get("batch_concurrency")
                )
                or 4,
            ),
            root_id=_normalize_optional_text(raw.get("root_id")) or "",
            nodes={
                str(node_id): AgentNode.from_raw(node_payload)
                for node_id, node_payload in nodes_raw.items()
                if isinstance(node_payload, dict)
            },
            final_artifact=(
                FinalArtifact.from_raw(raw.get("final_artifact"))
                if isinstance(raw.get("final_artifact"), dict)
                else None
            ),
            summary=RolloutSummary.from_raw(raw.get("summary", {})),
            result_path=_normalize_optional_text(raw.get("result_path")),
        )


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
