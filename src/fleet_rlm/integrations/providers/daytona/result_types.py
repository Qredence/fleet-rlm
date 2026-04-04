"""Recursive-task, result, and rollout-summary Daytona types.

Split from ``types.py`` for maintainability.  All public symbols are
re-exported by ``types.py`` so existing imports are unaffected.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .types import (
    ContextSource,
    PromptHandle,
    RolloutBudget,
    _coerce_nonnegative_int,
    _coerce_positive_int,
    _normalize_optional_text,
    _result_views,
)

# ---------------------------------------------------------------------------
# Section 4: Recursive-task and evidence-link types
# ---------------------------------------------------------------------------


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
    def from_raw(cls, raw: Any) -> TaskSourceProvenance:
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
    def from_raw(cls, raw: Any) -> RecursiveTaskSpec:
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
class DaytonaEvidenceRef:
    """Normalized evidence reference used for recursive child synthesis."""

    kind: str
    source_id: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    header: str | None = None
    preview: str | None = None
    chunk_index: int | None = None
    pattern: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> DaytonaEvidenceRef:
        if not isinstance(raw, dict):
            raise ValueError("Daytona evidence payload must be a dict.")

        line = _coerce_positive_int(raw.get("line"))
        start_line = _coerce_positive_int(raw.get("start_line")) or line
        end_line = _coerce_positive_int(raw.get("end_line")) or start_line
        if start_line is not None and end_line is not None and end_line < start_line:
            end_line = start_line

        evidence = cls(
            kind=_normalize_optional_text(raw.get("kind")) or "manual",
            source_id=_normalize_optional_text(raw.get("source_id")),
            path=_normalize_optional_text(raw.get("path")),
            start_line=start_line,
            end_line=end_line,
            header=_normalize_optional_text(raw.get("header")),
            preview=_normalize_optional_text(raw.get("preview"), limit=240),
            chunk_index=_coerce_nonnegative_int(raw.get("chunk_index")),
            pattern=_normalize_optional_text(raw.get("pattern")),
        )
        if evidence.source_id is None:
            evidence.source_id = evidence._derive_source_id()
        return evidence

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
        return f"evidence-{digest}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChildLink:
    """Persisted parent -> child recursive linkage."""

    child_id: str | None
    callback_name: str
    iteration: int | None
    task: RecursiveTaskSpec
    result_preview: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "callback_name": self.callback_name,
            "iteration": self.iteration,
            "task": self.task.to_dict(),
            "result_preview": self.result_preview,
            "status": self.status,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> ChildLink:
        if not isinstance(raw, dict):
            raise ValueError("Child link payload must be a dict.")
        return cls(
            child_id=_normalize_optional_text(raw.get("child_id")),
            callback_name=_normalize_optional_text(raw.get("callback_name"))
            or "llm_query",
            iteration=_coerce_positive_int(raw.get("iteration")),
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
    evidence: list[DaytonaEvidenceRef] = field(default_factory=list)
    confidence: float | None = None
    follow_up_needed: bool = False
    run_result: DaytonaRunResult | None = None


# ---------------------------------------------------------------------------
# Section 5: Result and rollout-summary types
# ---------------------------------------------------------------------------


def _dict_items(raw: Any, key: str) -> list[dict[str, Any]]:
    return [
        item
        for item in raw.get(key, []) or []
        if isinstance(raw, dict) and isinstance(item, dict)
    ]


def _string_items(raw: Any, key: str) -> list[str]:
    if not isinstance(raw, dict):
        return []
    return [str(item) for item in raw.get(key, []) or [] if item is not None]


def _context_sources_from_raw(raw: Any) -> list[ContextSource]:
    return [
        ContextSource.from_raw(item) for item in _dict_items(raw, "context_sources")
    ]


def _prompt_handles_from_raw(raw: Any) -> list[PromptHandle]:
    return [PromptHandle.from_raw(item) for item in _dict_items(raw, "prompt_handles")]


def _observations_from_raw(raw: Any) -> list[ExecutionObservation]:
    return [
        ExecutionObservation.from_raw(item) for item in _dict_items(raw, "observations")
    ]


def _child_links_from_raw(raw: Any) -> list[ChildLink]:
    return [ChildLink.from_raw(item) for item in _dict_items(raw, "child_links")]


def _warnings_from_raw(raw: Any) -> list[str]:
    return _string_items(raw, "warnings")


def _budget_from_raw(raw: Any) -> RolloutBudget:
    if not isinstance(raw, dict):
        raise ValueError("Daytona run result requires a budget dict.")
    return RolloutBudget(
        max_sandboxes=_coerce_positive_int(raw.get("max_sandboxes")) or 50,
        max_depth=_coerce_nonnegative_int(raw.get("max_depth")) or 2,
        max_iterations=_coerce_positive_int(raw.get("max_iterations")) or 50,
        global_timeout=_coerce_positive_int(raw.get("global_timeout")) or 3600,
        result_truncation_limit=_coerce_positive_int(raw.get("result_truncation_limit"))
        or 10_000,
        batch_concurrency=_coerce_positive_int(raw.get("batch_concurrency")) or 4,
    )


@dataclass(slots=True)
class FinalArtifact:
    """Structured final artifact produced by a node."""

    kind: str
    value: Any
    variable_name: str | None = None
    finalization_mode: str = "fallback"

    def to_dict(self) -> dict[str, Any]:
        return _result_views().final_artifact_dict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> FinalArtifact:
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
        return _result_views().execution_observation_dict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> ExecutionObservation:
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
        return _result_views().agent_node_dict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> AgentNode:
        if not isinstance(raw, dict):
            raise ValueError("Agent node payload must be a dict.")
        return cls(
            node_id=_normalize_optional_text(raw.get("node_id")) or "",
            parent_id=_normalize_optional_text(raw.get("parent_id")),
            depth=_coerce_nonnegative_int(raw.get("depth")) or 0,
            task=str(raw.get("task", "") or ""),
            repo=str(raw.get("repo", "") or ""),
            ref=_normalize_optional_text(raw.get("ref")),
            context_sources=_context_sources_from_raw(raw),
            sandbox_id=_normalize_optional_text(raw.get("sandbox_id")),
            workspace_path=_normalize_optional_text(raw.get("workspace_path")),
            status=_normalize_optional_text(raw.get("status")) or "running",
            prompt_handles=_prompt_handles_from_raw(raw),
            prompt_previews=_string_items(raw, "prompt_previews"),
            response_previews=_string_items(raw, "response_previews"),
            observations=_observations_from_raw(raw),
            child_ids=_string_items(raw, "child_ids"),
            child_links=_child_links_from_raw(raw),
            warnings=_warnings_from_raw(raw),
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
    phase_timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _result_views().rollout_summary_dict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> RolloutSummary:
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
            phase_timings_ms={
                str(key): _coerce_nonnegative_int(value) or 0
                for key, value in (raw.get("phase_timings_ms", {}) or {}).items()
                if key is not None
            },
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
    evaluation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _result_views().daytona_run_result_dict(self)

    def to_public_dict(self) -> dict[str, Any]:
        return _result_views().daytona_run_result_public_dict(self)

    def node_evaluation(self, node_id: str) -> dict[str, Any]:
        nodes = self.evaluation.get("nodes", {})
        if not isinstance(nodes, dict):
            return {}
        payload = nodes.get(node_id, {})
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): list(value) if isinstance(value, list) else value
            for key, value in payload.items()
        }

    @classmethod
    def from_raw(cls, raw: Any) -> DaytonaRunResult:
        if not isinstance(raw, dict):
            raise ValueError("Daytona run result payload must be a dict.")
        nodes_raw = raw.get("nodes")
        if not isinstance(nodes_raw, dict):
            raise ValueError("Daytona run result requires nodes.")
        return cls(
            run_id=_normalize_optional_text(raw.get("run_id")) or "",
            repo=str(raw.get("repo", "") or ""),
            ref=_normalize_optional_text(raw.get("ref")),
            context_sources=_context_sources_from_raw(raw),
            task=str(raw.get("task", "") or ""),
            budget=_budget_from_raw(raw.get("budget")),
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
            evaluation=raw.get("evaluation")
            if isinstance(raw.get("evaluation"), dict)
            else {},
        )


@dataclass(slots=True)
class DaytonaSmokeResult:
    """Result of a Daytona live/runtime smoke check."""

    repo: str
    ref: str | None
    sandbox_id: str | None
    workspace_path: str = ""
    persisted_state_value: Any = None
    driver_started: bool = False
    finalization_mode: str = "unknown"
    termination_phase: str = "config"
    error_category: str | None = None
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _result_views().daytona_smoke_result_dict(self)


__all__ = [
    # Recursive types
    "TaskSourceProvenance",
    "RecursiveTaskSpec",
    "DaytonaEvidenceRef",
    "ChildLink",
    "ChildTaskResult",
    # Result types
    "FinalArtifact",
    "ExecutionObservation",
    "AgentNode",
    "RolloutSummary",
    "DaytonaRunResult",
    "DaytonaSmokeResult",
]
