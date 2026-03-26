"""Public Daytona result-view helpers extracted from the result dataclasses."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .types_context import ContextSource
from .types_recursive import ChildLink
from .types_serialization import _persisted_text_preview


def final_artifact_dict(artifact: Any) -> dict[str, Any]:
    return asdict(artifact)


def execution_observation_dict(observation: Any) -> dict[str, Any]:
    return asdict(observation)


def agent_node_dict(node: Any) -> dict[str, Any]:
    payload = asdict(node)
    payload["task"] = _persisted_text_preview(node.task)
    payload["context_sources"] = [item.to_dict() for item in node.context_sources]
    payload["prompt_handles"] = [item.to_dict() for item in node.prompt_handles]
    payload["observations"] = [item.to_dict() for item in node.observations]
    payload["child_links"] = [item.to_dict() for item in node.child_links]
    payload["warnings"] = list(node.warnings)
    payload["final_artifact"] = (
        node.final_artifact.to_dict() if node.final_artifact is not None else None
    )
    return payload


def rollout_summary_dict(summary: Any) -> dict[str, Any]:
    return asdict(summary)


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


def daytona_run_result_dict(result: Any) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "repo": result.repo,
        "ref": result.ref,
        "context_sources": [item.to_dict() for item in result.context_sources],
        "task": _persisted_text_preview(result.task),
        "budget": asdict(result.budget),
        "root_id": result.root_id,
        "nodes": {node_id: node.to_dict() for node_id, node in result.nodes.items()},
        "final_artifact": (
            result.final_artifact.to_dict()
            if result.final_artifact is not None
            else None
        ),
        "summary": result.summary.to_dict(),
        "result_path": result.result_path,
        "evaluation": result.evaluation,
    }


def _callback_counts_by_iteration(root: Any | None) -> dict[int, int]:
    counts: dict[int, int] = {}
    if root is None:
        return counts
    for link in root.child_links:
        if link.iteration is None:
            continue
        counts[link.iteration] = counts.get(link.iteration, 0) + 1
    return counts


def daytona_run_result_public_iterations(
    result: Any,
    root: Any | None,
) -> list[dict[str, Any]]:
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
    callback_counts = _callback_counts_by_iteration(root)

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
            "reasoning_summary": _persisted_text_preview(response_preview, limit=1200),
            "code": observation.code if observation is not None else "",
            "stdout": observation.stdout if observation is not None else "",
            "stderr": observation.stderr if observation is not None else "",
            "error": observation.error if observation is not None else None,
            "duration_ms": observation.duration_ms if observation is not None else None,
            "callback_count": callback_counts.get(
                iteration,
                observation.callback_count if observation is not None else 0,
            ),
        }
        if (
            result.final_artifact is not None
            and iteration == root.iteration_count
            and result.summary.termination_reason == "completed"
        ):
            record["finalized"] = True
        iterations.append(record)

    return iterations


def daytona_run_result_public_callbacks(
    root: Any | None,
) -> list[dict[str, Any]]:
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


def daytona_run_result_public_sources(
    result: Any,
    root: Any | None,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source in result.context_sources:
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


def daytona_run_result_public_attachments(
    result: Any,
) -> list[dict[str, Any]]:
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
        for source in result.context_sources
    ]


def daytona_run_result_public_dict(result: Any) -> dict[str, Any]:
    root = result.nodes.get(result.root_id)
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
        "run_id": result.run_id,
        "repo": result.repo,
        "ref": result.ref,
        "task": _persisted_text_preview(result.task),
        "daytona_mode": "host_loop_rlm",
        "root_id": result.root_id,
        "nodes": {node_id: node.to_dict() for node_id, node in result.nodes.items()},
        "context_sources": [item.to_dict() for item in result.context_sources],
        "prompts": [handle.to_dict() for handle in prompt_handles],
        "iterations": daytona_run_result_public_iterations(result, root),
        "callbacks": daytona_run_result_public_callbacks(root),
        "sources": daytona_run_result_public_sources(result, root),
        "attachments": daytona_run_result_public_attachments(result),
        "final_artifact": (
            result.final_artifact.to_dict()
            if result.final_artifact is not None
            else None
        ),
        "summary": result.summary.to_dict(),
        "result_path": result.result_path,
    }


def daytona_smoke_result_dict(result: Any) -> dict[str, Any]:
    return asdict(result)


__all__ = [
    "_child_source_public_entry",
    "agent_node_dict",
    "_context_source_public_entry",
    "execution_observation_dict",
    "final_artifact_dict",
    "daytona_run_result_dict",
    "daytona_run_result_public_attachments",
    "daytona_run_result_public_callbacks",
    "daytona_run_result_public_dict",
    "daytona_run_result_public_iterations",
    "daytona_run_result_public_sources",
    "rollout_summary_dict",
    "daytona_smoke_result_dict",
]
