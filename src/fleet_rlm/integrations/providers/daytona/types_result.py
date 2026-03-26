"""Result and rollout-summary types for Daytona execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .types_budget import RolloutBudget
from .types_context import ContextSource, PromptHandle
from .types_recursive import ChildLink
from .types_serialization import (
    _coerce_nonnegative_int,
    _coerce_positive_int,
    _normalize_optional_text,
    _persisted_text_preview,
)


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


def _context_source_public_entry(source: ContextSource) -> dict[str, Any]:
    title = Path(source.host_path).name or source.host_path
    description_parts = [f"Staged at {source.staged_path}"]
    if source.source_type:
        description_parts.append(f"type: {source.source_type}")
    if source.extraction_method:
        description_parts.append(f"extracted via {source.extraction_method}")
    if source.file_count > 1:
        description_parts.append(f"{source.file_count} files")
    if source.skipped_count:
        description_parts.append(f"{source.skipped_count} skipped")
    if source.warnings:
        description_parts.extend(source.warnings)
    return {
        "source_id": source.source_id,
        "kind": "file",
        "title": title,
        "display_url": source.host_path,
        "description": "; ".join(part for part in description_parts if part),
        "quote": None,
        "host_path": source.host_path,
        "staged_path": source.staged_path,
        "source_type": source.source_type,
        "extraction_method": source.extraction_method,
    }


def _child_source_public_entry(link: ChildLink, index: int) -> dict[str, Any] | None:
    source = link.task.source
    if source.path is None and source.preview is None:
        return None
    line_start = source.start_line or source.line
    line_end = source.end_line or line_start
    span = (
        f"lines {line_start}-{line_end}"
        if line_start is not None and line_end is not None
        else None
    )
    details = [
        span,
        f"header: {source.header}" if source.header else None,
        f"pattern: {source.pattern}" if source.pattern else None,
    ]
    title = source.path or source.source_id or link.task.label or link.task.task
    return {
        "source_id": source.source_id or f"source-{index}",
        "kind": "file",
        "title": title,
        "display_url": source.path,
        "description": "; ".join(part for part in details if part is not None) or None,
        "quote": source.preview,
        "path": source.path,
        "start_line": line_start,
        "end_line": line_end,
        "chunk_index": source.chunk_index,
        "header": source.header,
        "pattern": source.pattern,
    }


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
        return asdict(self)

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
        return asdict(self)

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
            "evaluation": self.evaluation,
        }

    def to_public_dict(self) -> dict[str, Any]:
        root = self.nodes.get(self.root_id)
        prompt_handles = list(root.prompt_handles) if root is not None else []
        prompt_handles = sorted(
            prompt_handles,
            key=lambda handle: (
                handle.kind or "",
                handle.label or "",
                handle.handle_id,
            ),
        )

        return {
            "run_id": self.run_id,
            "repo": self.repo,
            "ref": self.ref,
            "task": _persisted_text_preview(self.task),
            "daytona_mode": "host_loop_rlm",
            "root_id": self.root_id,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "context_sources": [item.to_dict() for item in self.context_sources],
            "prompts": [handle.to_dict() for handle in prompt_handles],
            "iterations": self._public_iterations(root),
            "callbacks": self._public_callbacks(root),
            "sources": self._public_sources(root),
            "attachments": self._public_attachments(),
            "final_artifact": (
                self.final_artifact.to_dict()
                if self.final_artifact is not None
                else None
            ),
            "summary": self.summary.to_dict(),
            "result_path": self.result_path,
        }

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

    def _public_iterations(self, root: AgentNode | None) -> list[dict[str, Any]]:
        if root is None:
            return []

        iterations: list[dict[str, Any]] = []
        observation_by_iteration = {
            observation.iteration: observation for observation in root.observations
        }
        max_iterations = max(
            [
                root.iteration_count,
                *observation_by_iteration.keys(),
            ],
            default=0,
        )
        callback_counts = self._callback_counts_by_iteration(root)

        for iteration in range(1, max_iterations + 1):
            observation = observation_by_iteration.get(iteration)
            response_preview = (
                root.response_previews[iteration - 1]
                if iteration - 1 < len(root.response_previews)
                else ""
            )
            record: dict[str, Any] = {
                "iteration": iteration,
                "status": (
                    "error"
                    if observation is not None and observation.error
                    else "completed"
                ),
                "reasoning_summary": _normalize_optional_text(
                    response_preview, limit=1200
                ),
                "code": observation.code if observation is not None else "",
                "stdout": observation.stdout if observation is not None else "",
                "stderr": observation.stderr if observation is not None else "",
                "error": observation.error if observation is not None else None,
                "duration_ms": (
                    observation.duration_ms if observation is not None else None
                ),
                "callback_count": callback_counts.get(
                    iteration,
                    observation.callback_count if observation is not None else 0,
                ),
            }
            if (
                self.final_artifact is not None
                and iteration == root.iteration_count
                and self.summary.termination_reason == "completed"
            ):
                record["finalized"] = True
            iterations.append(record)

        return iterations

    def _public_callbacks(self, root: AgentNode | None) -> list[dict[str, Any]]:
        if root is None:
            return []
        callbacks: list[dict[str, Any]] = []
        for index, link in enumerate(root.child_links, start=1):
            callbacks.append(
                {
                    "id": f"callback-{index}",
                    "callback_name": link.callback_name,
                    "iteration": link.iteration,
                    "status": link.status,
                    "task": link.task.task,
                    "label": link.task.label,
                    "result_preview": link.result_preview,
                    "source": link.task.source.to_dict(),
                }
            )
        return callbacks

    def _callback_counts_by_iteration(self, root: AgentNode | None) -> dict[int, int]:
        counts: dict[int, int] = {}
        if root is None:
            return counts
        for link in root.child_links:
            if link.iteration is None:
                continue
            counts[link.iteration] = counts.get(link.iteration, 0) + 1
        return counts

    def _public_sources(self, root: AgentNode | None) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()

        for source in self.context_sources:
            key = f"context:{source.source_id}"
            if key in seen:
                continue
            seen.add(key)
            sources.append(_context_source_public_entry(source))

        if root is None:
            return sources

        for link in root.child_links:
            source = link.task.source
            key = (
                f"task:{source.source_id or ''}:{source.path or ''}:"
                f"{source.start_line or source.line or ''}:{source.end_line or ''}:"
                f"{source.chunk_index if source.chunk_index is not None else ''}:"
                f"{source.header or ''}:{source.pattern or ''}"
            )
            if key in seen:
                continue
            seen.add(key)
            entry = _child_source_public_entry(link, len(sources) + 1)
            if entry is not None:
                sources.append(entry)

        return sources

    def _public_attachments(self) -> list[dict[str, Any]]:
        return [
            {
                "attachment_id": source.source_id,
                "name": Path(source.host_path).name or source.host_path,
                "kind": source.kind,
                "mime_type": source.source_type,
                "description": (
                    f"Host path: {source.host_path}"
                    if source.staged_path == source.host_path
                    else f"Host path: {source.host_path}; staged at {source.staged_path}"
                ),
            }
            for source in self.context_sources
        ]

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
        return asdict(self)


__all__ = [
    "FinalArtifact",
    "ExecutionObservation",
    "AgentNode",
    "RolloutSummary",
    "DaytonaRunResult",
    "DaytonaSmokeResult",
]
