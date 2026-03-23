"""Sandbox, RLM, memory, buffer, and volume tool definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.signatures import (
    GroundedCitation,
    MemoryMigrationOperation,
    VolumeTreeNode,
)
from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .runtime_module_helpers import coerce_int as _coerce_int
from .runtime_module_helpers import coerce_str_list as _coerce_str_list
from .runtime_module_helpers import prediction_value as _prediction_value
from .runtime_module_helpers import run_cached_runtime_module as _run_runtime_module
from .runtime_module_helpers import runtime_metadata as _runtime_metadata
from .shared import (
    aexecute_submit,
    build_trajectory_payload,
    chunk_text,
    chunk_to_text,
    execute_submit,
    resolve_document,
)
from .volume_helpers import resolve_mounted_volume_path

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _SandboxToolContext:
    """Shared context for sandbox/volume tool operations."""

    agent: RLMReActChatAgent


def _execute_submit_ctx(
    ctx: _SandboxToolContext, code: str, *, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    return execute_submit(ctx.agent, code, variables=variables or {})


async def _aexecute_submit_ctx(
    ctx: _SandboxToolContext, code: str, *, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await aexecute_submit(ctx.agent, code, variables=variables or {})


def _resolve_path_or_error(
    *,
    path: str,
    default_root: str,
    allowed_root: str = "/data",
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        return (
            resolve_mounted_volume_path(
                path,
                default_root=default_root,
                allowed_root=allowed_root,
            ),
            None,
        )
    except ValueError as exc:
        return None, {"status": "error", "error": str(exc)}


def _persistent_roots(ctx: _SandboxToolContext) -> tuple[str, str, str]:
    """Return the allowed root plus memory/workspace defaults for the backend."""
    roots = runtime_storage_roots(ctx.agent.interpreter)
    return roots.allowed_root, roots.memory_root, roots.workspace_root


def _reload_volume_best_effort(ctx: _SandboxToolContext) -> None:
    if ctx.agent.interpreter._volume:
        try:
            ctx.agent.interpreter.reload()
        except Exception:
            logger.warning("Best-effort volume reload failed", exc_info=True)


def _commit_volume_best_effort(ctx: _SandboxToolContext) -> None:
    if ctx.agent.interpreter._volume:
        try:
            ctx.agent.interpreter.commit()
        except Exception as exc:
            logger.exception("Best-effort volume commit failed: %s", exc)


def _normalize_grounded_citations(value: Any) -> list[GroundedCitation]:
    """Normalize grounded-answer citations into the canonical DSPy shape."""
    if not isinstance(value, list):
        return []

    citations: list[GroundedCitation] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        citation: GroundedCitation = {
            "source": str(item.get("source", "")).strip(),
            "chunk_id": str(item.get("chunk_id", item.get("chunkId", ""))).strip(),
            "evidence": str(item.get("evidence", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        }
        if any(citation.values()):
            citations.append(citation)

    return citations


async def _aget_daytona_session(ctx: _SandboxToolContext) -> Any | None:
    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:
        return None

    interpreter = ctx.agent.interpreter
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    return await interpreter._aensure_session()


def _daytona_file_error(*, path: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
        "path": path,
    }


def _is_daytona_missing_file_error(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return True
    message = str(exc).lower()
    return "no such file" in message or "not found" in message


async def _adaytona_read_text(daytona_session: Any, path: str) -> str:
    return await daytona_session.aread_file(path)


async def _adaytona_write_text(
    daytona_session: Any,
    path: str,
    content: str,
    *,
    append: bool = False,
) -> str:
    payload = content
    if append:
        try:
            payload = await _adaytona_read_text(daytona_session, path) + content
        except Exception as exc:
            if not _is_daytona_missing_file_error(exc):
                raise
    return await daytona_session.awrite_file(path, payload)


async def _adaytona_list_items(daytona_session: Any, path: str) -> list[dict[str, str]]:
    if hasattr(daytona_session, "alist_files"):
        entries = await daytona_session.alist_files(path)
    elif hasattr(daytona_session, "list_files"):
        entries = daytona_session.list_files(path)
        if hasattr(entries, "__await__"):
            entries = await entries
    else:
        list_files = getattr(getattr(daytona_session, "sandbox", None), "fs", None)
        if list_files is None or not hasattr(list_files, "list_files"):
            raise AttributeError(
                f"{type(daytona_session).__name__!r} object has no async Daytona file-listing API"
            )
        entries = list_files.list_files(path)
        if hasattr(entries, "__await__"):
            entries = await entries
    items: list[dict[str, str]] = []
    for entry in entries:
        name = str(getattr(entry, "name", "") or "")
        if not name:
            continue
        items.append(
            {
                "name": name,
                "type": "dir" if bool(getattr(entry, "is_dir", False)) else "file",
            }
        )
    return items


@dataclass(slots=True)
class _DelegateToolContext:
    """Shared context for RLM delegation tool callables."""

    agent: RLMReActChatAgent


def build_rlm_delegate_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build cached-runtime and recursive delegation tools bound to *agent*."""
    ctx = _DelegateToolContext(agent=agent)

    async def parallel_semantic_map(
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = resolve_document(ctx.agent, "active")
        chunks = chunk_text(
            text, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        chunk_texts = [chunk_to_text(chunk) for chunk in chunks][:max_chunks]
        prompts = [
            "Query: "
            f"{query}\nChunk index: {idx}\nReturn concise findings as plain text.\n\n"
            f"{chunk_text[:6000]}"
            for idx, chunk_text in enumerate(chunk_texts)
        ]

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return await aexecute_submit(
            ctx.agent,
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    async def analyze_long_document(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "analyze_long_document",
            document=document,
            query=query,
        )
        if error is not None:
            return error

        return {
            "status": "ok",
            "findings": _coerce_str_list(_prediction_value(prediction, "findings", [])),
            "answer": str(_prediction_value(prediction, "answer", "")),
            "sections_examined": _coerce_int(
                _prediction_value(prediction, "sections_examined", 0),
                default=0,
                minimum=0,
            ),
            "doc_chars": len(document),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def summarize_long_document(
        focus: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "summarize_long_document",
            document=document,
            focus=focus,
        )
        if error is not None:
            return error

        return {
            "status": "ok",
            "summary": str(_prediction_value(prediction, "summary", "")),
            "key_points": _coerce_str_list(
                _prediction_value(prediction, "key_points", [])
            ),
            "coverage_pct": _coerce_int(
                _prediction_value(prediction, "coverage_pct", 0),
                default=0,
                minimum=0,
                maximum=100,
            ),
            "doc_chars": len(document),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def extract_from_logs(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "extract_from_logs",
            logs=document,
            query=query,
        )
        if error is not None:
            return error

        raw_patterns = _prediction_value(prediction, "patterns", {})
        if isinstance(raw_patterns, dict):
            patterns: dict[str, str] | list[str] = {
                str(key): str(value) for key, value in raw_patterns.items()
            }
        elif isinstance(raw_patterns, list):
            patterns = _coerce_str_list(raw_patterns)
        else:
            patterns = {}

        return {
            "status": "ok",
            "matches": _coerce_str_list(_prediction_value(prediction, "matches", [])),
            "patterns": patterns,
            "time_range": str(_prediction_value(prediction, "time_range", "unknown")),
            "doc_chars": len(document),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def grounded_answer(
        query: str,
        alias: str = "active",
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        max_chunks_int = _coerce_int(max_chunks, default=-1)
        if max_chunks_int <= 0:
            return {"status": "error", "error": "Invalid max_chunks value."}

        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "grounded_answer",
            document=document,
            query=query,
            chunk_strategy=chunk_strategy,
            max_chunks=max_chunks_int,
            response_style="concise",
        )
        if error is not None:
            return error

        citations = _normalize_grounded_citations(
            _prediction_value(prediction, "citations", [])
        )

        return {
            "status": "ok",
            "answer": str(_prediction_value(prediction, "answer", "")),
            "citations": citations,
            "confidence": _coerce_int(
                _prediction_value(prediction, "confidence", 0),
                default=0,
                minimum=0,
                maximum=100,
            ),
            "coverage_notes": str(_prediction_value(prediction, "coverage_notes", "")),
            "doc_chars": len(document),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "triage_incident_logs",
            logs=document,
            service_context=service_context,
            query=query,
        )
        if error is not None:
            return error

        severity = str(_prediction_value(prediction, "severity", "low")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "low"

        return {
            "status": "ok",
            "severity": severity,
            "probable_root_causes": _coerce_str_list(
                _prediction_value(prediction, "probable_root_causes", [])
            ),
            "impacted_components": _coerce_str_list(
                _prediction_value(prediction, "impacted_components", [])
            ),
            "recommended_actions": _coerce_str_list(
                _prediction_value(prediction, "recommended_actions", [])
            ),
            "time_range": str(_prediction_value(prediction, "time_range", "unknown")),
            "doc_chars": len(document),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def plan_code_change(
        task: str,
        repo_context: str = "",
        constraints: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "plan_code_change",
            task=task,
            repo_context=repo_context,
            constraints=constraints or "Keep changes minimal.",
        )
        if error is not None:
            return error

        return {
            "status": "ok",
            "plan_steps": _coerce_str_list(
                _prediction_value(prediction, "plan_steps", [])
            ),
            "files_to_touch": _coerce_str_list(
                _prediction_value(prediction, "files_to_touch", [])
            ),
            "validation_commands": _coerce_str_list(
                _prediction_value(prediction, "validation_commands", [])
            ),
            "risks": _coerce_str_list(_prediction_value(prediction, "risks", [])),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        turn_lines = [
            f"Turn {idx}\n{turn}"
            for idx, turn in enumerate(ctx.agent.history_messages()[-20:], 1)
        ]
        turn_history = "\n\n".join(turn_lines) or "No recent turns."
        prediction, error, fallback_used = _run_runtime_module(
            ctx.agent,
            "propose_core_memory_update",
            turn_history=turn_history,
            current_memory=ctx.agent.fmt_core_memory(),
        )
        if error is not None:
            return error

        return {
            "status": "ok",
            "keep": _coerce_str_list(_prediction_value(prediction, "keep", [])),
            "update": _coerce_str_list(_prediction_value(prediction, "update", [])),
            "remove": _coerce_str_list(_prediction_value(prediction, "remove", [])),
            "rationale": str(_prediction_value(prediction, "rationale", "")),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        result = await spawn_delegate_sub_agent_async(
            ctx.agent,
            prompt=query,
            context=context,
            stream_event_callback=getattr(ctx.agent, "_live_event_callback", None),
        )
        if result.get("status") == "error":
            return result
        return {
            "status": "ok",
            "answer": result.get("answer") or result.get("assistant_response", ""),
            "sub_agent_history": result.get("sub_agent_history", 0),
            "depth": result.get("depth", getattr(ctx.agent, "_current_depth", 0) + 1),
            **build_trajectory_payload(result, include_trajectory=True),
        }

    from dspy import Tool

    return [
        Tool(
            _sync_compatible_tool_callable(parallel_semantic_map),
            name="parallel_semantic_map",
            desc="Run batched semantic analysis over document chunks and store findings in a buffer",
        ),
        Tool(
            _sync_compatible_tool_callable(analyze_long_document),
            name="analyze_long_document",
            desc="Analyze a long document with the cached runtime module and return findings plus an answer",
        ),
        Tool(
            _sync_compatible_tool_callable(summarize_long_document),
            name="summarize_long_document",
            desc="Summarize a long document with key points and coverage metadata",
        ),
        Tool(
            _sync_compatible_tool_callable(extract_from_logs),
            name="extract_from_logs",
            desc="Extract structured matches and patterns from log text",
        ),
        Tool(
            _sync_compatible_tool_callable(grounded_answer),
            name="grounded_answer",
            desc="Answer a query with explicit machine-readable citations",
        ),
        Tool(
            _sync_compatible_tool_callable(triage_incident_logs),
            name="triage_incident_logs",
            desc="Classify log severity, causes, impact, and recommended actions",
        ),
        Tool(
            _sync_compatible_tool_callable(plan_code_change),
            name="plan_code_change",
            desc="Build a structured code-change plan with files, risks, and validation commands",
        ),
        Tool(
            _sync_compatible_tool_callable(propose_core_memory_update),
            name="propose_core_memory_update",
            desc="Propose safe updates to core memory based on recent conversation turns",
        ),
        Tool(
            _sync_compatible_tool_callable(rlm_query),
            name="rlm_query",
            desc="Delegate a deep symbolic task to a bounded child RLM runtime",
        ),
    ]


@dataclass(slots=True)
class _MemoryIntelligenceContext:
    """Shared context for memory intelligence tool operations."""

    agent: RLMReActChatAgent


async def _reload_memory_volume_best_effort(
    ctx: _MemoryIntelligenceContext, *, reason: str
) -> None:
    if ctx.agent.interpreter._volume:
        try:
            await ctx.agent.interpreter.areload()
        except Exception as exc:
            logger.warning(
                "Failed to reload interpreter volume for %s: %s", reason, exc
            )


def _resolve_memory_root(root_path: str) -> tuple[str | None, dict[str, Any] | None]:
    try:
        return (
            resolve_mounted_volume_path(
                root_path,
                default_root="/data/memory",
                allowed_root="/data",
            ),
            None,
        )
    except ValueError as exc:
        return None, {"status": "error", "error": str(exc)}


def _normalize_tree_nodes(value: Any) -> list[VolumeTreeNode]:
    if not isinstance(value, list):
        return []

    nodes: list[VolumeTreeNode] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        nodes.append(
            VolumeTreeNode(
                path=str(item.get("path", "")),
                type=str(item.get("type", "")),
                size_bytes=_coerce_int(item.get("size_bytes", 0), minimum=0),
                depth=_coerce_int(item.get("depth", 0), minimum=0),
            )
        )
    return nodes


def _normalize_memory_migration_operations(
    value: Any,
) -> list[MemoryMigrationOperation]:
    """Normalize migration-plan operations into the canonical DSPy shape."""
    if not isinstance(value, list):
        return []

    operations: list[MemoryMigrationOperation] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        operation: MemoryMigrationOperation = {
            "op": str(item.get("op", "")).strip(),
            "src": str(item.get("src", "")).strip(),
            "dst": str(item.get("dst", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        }
        if any(operation.values()):
            operations.append(operation)

    return operations


def _normalize_non_empty_strings(value: Any) -> list[str]:
    """Normalize list-like values into stripped, non-empty unique strings."""
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def build_memory_intelligence_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build memory-analysis tools backed by cached runtime modules."""
    from dspy import Tool

    ctx = _MemoryIntelligenceContext(agent=agent)

    async def memory_tree(
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        resolved_path, error = _resolve_memory_root(root_path)
        if error is not None:
            return error

        await _reload_memory_volume_best_effort(ctx, reason="memory_tree")
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_tree",
            root_path=resolved_path,
            max_depth=_coerce_int(max_depth, default=4, minimum=0, maximum=12),
            include_hidden=bool(include_hidden),
        )
        if runtime_error is not None:
            return runtime_error

        nodes = _normalize_tree_nodes(_prediction_value(prediction, "nodes", []))
        return {
            "status": "ok",
            "root_path": resolved_path,
            "nodes": nodes,
            "total_files": _coerce_int(
                _prediction_value(
                    prediction,
                    "total_files",
                    len([node for node in nodes if node["type"] == "file"]),
                ),
                minimum=0,
            ),
            "total_dirs": _coerce_int(
                _prediction_value(
                    prediction,
                    "total_dirs",
                    len([node for node in nodes if node["type"] == "dir"]),
                ),
                minimum=0,
            ),
            "truncated": bool(_prediction_value(prediction, "truncated", False)),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_action_intent(
        user_request: str,
        policy_constraints: str = "",
    ) -> dict[str, Any]:
        constraints_text = (
            policy_constraints
            or "Prefer non-destructive operations and ask for confirmation on risky actions."
        )
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_action_intent",
            user_request=user_request,
            policy_constraints=constraints_text,
        )
        if runtime_error is not None:
            return runtime_error

        risk_level = (
            str(_prediction_value(prediction, "risk_level", "medium")).strip().lower()
        )
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"

        action_type = (
            str(_prediction_value(prediction, "action_type", "noop")).strip().lower()
        )
        if action_type not in {
            "read",
            "write",
            "append",
            "move",
            "delete",
            "mkdir",
            "tree",
            "audit",
            "migrate",
            "noop",
        }:
            action_type = "noop"

        return {
            "status": "ok",
            "action_type": action_type,
            "target_paths": _normalize_non_empty_strings(
                _prediction_value(prediction, "target_paths", [])
            ),
            "content_plan": _normalize_non_empty_strings(
                _prediction_value(prediction, "content_plan", [])
            ),
            "risk_level": risk_level,
            "requires_confirmation": bool(
                _prediction_value(prediction, "requires_confirmation", True)
            ),
            "rationale": str(_prediction_value(prediction, "rationale", "")).strip(),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_audit(usage_goals: str = "") -> dict[str, Any]:
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_structure_audit",
            usage_goals=(
                usage_goals
                or "Keep memory discoverable, consistent, and easy to maintain."
            ),
        )
        if runtime_error is not None:
            return runtime_error

        return {
            "status": "ok",
            "issues": _normalize_non_empty_strings(
                _prediction_value(prediction, "issues", [])
            ),
            "recommended_layout": _normalize_non_empty_strings(
                _prediction_value(prediction, "recommended_layout", [])
            ),
            "naming_conventions": _normalize_non_empty_strings(
                _prediction_value(prediction, "naming_conventions", [])
            ),
            "retention_rules": _normalize_non_empty_strings(
                _prediction_value(prediction, "retention_rules", [])
            ),
            "priority_fixes": _normalize_non_empty_strings(
                _prediction_value(prediction, "priority_fixes", [])
            ),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_migration_plan(
        approved_constraints: str = "",
    ) -> dict[str, Any]:
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_structure_migration_plan",
            approved_constraints=(
                approved_constraints
                or "No destructive operation without explicit confirmation and rollback."
            ),
        )
        if runtime_error is not None:
            return runtime_error

        operations = _normalize_memory_migration_operations(
            _prediction_value(prediction, "operations", [])
        )

        estimated_risk = (
            str(_prediction_value(prediction, "estimated_risk", "medium"))
            .strip()
            .lower()
        )
        if estimated_risk not in {"low", "medium", "high"}:
            estimated_risk = "medium"

        return {
            "status": "ok",
            "operations": operations,
            "rollback_steps": _normalize_non_empty_strings(
                _prediction_value(prediction, "rollback_steps", [])
            ),
            "verification_checks": _normalize_non_empty_strings(
                _prediction_value(prediction, "verification_checks", [])
            ),
            "estimated_risk": estimated_risk,
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def clarification_questions(
        request: str,
        operation_risk: str = "medium",
    ) -> dict[str, Any]:
        risk_norm = operation_risk.strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "clarification_questions",
            request=request,
            operation_risk=risk_norm,
        )
        if runtime_error is not None:
            return runtime_error

        proceed_without_answer = bool(
            _prediction_value(prediction, "proceed_without_answer", False)
        )
        if risk_norm == "high":
            proceed_without_answer = False

        return {
            "status": "ok",
            "questions": _normalize_non_empty_strings(
                _prediction_value(prediction, "questions", [])
            ),
            "blocking_unknowns": _normalize_non_empty_strings(
                _prediction_value(prediction, "blocking_unknowns", [])
            ),
            "safe_default": str(
                _prediction_value(prediction, "safe_default", "")
            ).strip(),
            "proceed_without_answer": proceed_without_answer,
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    return [
        Tool(
            _sync_compatible_tool_callable(memory_tree),
            name="memory_tree",
            desc="Return a bounded file-tree snapshot for a path in Modal volume memory",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_action_intent),
            name="memory_action_intent",
            desc="Infer memory action intent, risk, and confirmation needs from a request",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_structure_audit),
            name="memory_structure_audit",
            desc="Audit memory layout and recommend structure conventions",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_structure_migration_plan),
            name="memory_structure_migration_plan",
            desc="Generate reversible migration operations from memory audit findings",
        ),
        Tool(
            _sync_compatible_tool_callable(clarification_questions),
            name="clarification_questions",
            desc="Generate clarification questions for ambiguous or risky memory operations",
        ),
    ]


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_sandbox_tools(
    agent: RLMReActChatAgent,
) -> list[Any]:
    """Build sandbox / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """
    ctx = _SandboxToolContext(agent=agent)
    tools: list[Any] = []

    # -- RLM delegation tools ------------------------------------------------
    tools.extend(build_rlm_delegate_tools(agent))

    # -- Memory intelligence tools -------------------------------------------
    tools.extend(build_memory_intelligence_tools(agent))

    # -- Sandbox editing -----------------------------------------------------

    async def edit_file(
        path: str, old_snippet: str, new_snippet: str
    ) -> dict[str, Any]:
        """Robustly edit a file by finding and replacing a unique text snippet.

        Fails if the old_snippet is not found or is not unique in the file.
        Use this over fragile `sed` commands for precise code editing.
        """
        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
    exit(0)

count = content.count(old_snippet)
if count == 0:
    SUBMIT(status="error", error="old_snippet not found in file")
elif count > 1:
    SUBMIT(status="error", error=f"old_snippet is ambiguous (found {count} times)")
else:
    new_content = content.replace(old_snippet, new_snippet)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    SUBMIT(status="ok", path=path, message="File updated successfully")
"""
        return await _aexecute_submit_ctx(
            ctx,
            code,
            variables={
                "path": path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
            },
        )

    # -- Buffer & volume management ------------------------------------------

    async def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = await _aexecute_submit_ctx(
            ctx,
            "SUBMIT(items=get_buffer(name))",
            variables={"name": name},
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    async def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return await _aexecute_submit_ctx(ctx, code, variables=variables)

    async def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to persistent storage as JSON."""
        allowed_root, _memory_root, workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=str(PurePosixPath(workspace_root) / "buffers"),
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            result = await _aexecute_submit_ctx(
                ctx,
                "SUBMIT(items=get_buffer(name))",
                variables={"name": name},
            )
            items = result.get("items", [])
            payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    payload,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {"status": "ok", "saved_path": saved_path, "item_count": len(items)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from persistent storage into host-side document memory."""
        allowed_root, _memory_root, workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=workspace_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                text = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            ctx.agent._set_document(alias, text)
            ctx.agent.active_alias = alias
            return {
                "status": "ok",
                "alias": alias,
                "path": resolved_path,
                "chars": len(text),
                "lines": len(text.splitlines()),
            }

        # Pull latest writes from other containers before reading.
        _reload_volume_best_effort(ctx)

        result = await _aexecute_submit_ctx(
            ctx,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        ctx.agent._set_document(alias, text)
        ctx.agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": resolved_path,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    async def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a document from volume and register it for downstream analysis."""
        loaded = await load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = ctx.agent.documents.get(alias, "")
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Use analyze_long_document or summarize_long_document for semantic processing.",
        }

    # -- Persistent memory management ----------------------------------------
    # These tools allow the agent to use the mounted volume as a persistent
    # "hard drive" for storing user profiles, archival documents, etc.

    async def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent storage."""
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                content = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "content": content,
                "chars": len(content),
            }

        _reload_volume_best_effort(ctx)

        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    SUBMIT(status="ok", path=path, content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return await _aexecute_submit_ctx(ctx, code, variables={"path": resolved_path})

    async def memory_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in persistent storage."""
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                await _adaytona_write_text(daytona_session, resolved_path, content)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "chars": len(content),
            }

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # Sync filesystem to volume if supported (optional but good for safety)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        # Note: Modal volumes are eventually consistent, but os.sync() helps.
        # The Interpreter also exposes a .commit() method if needed on the host side.
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"path": resolved_path, "content": content},
        )

        # Trigger explicit commit on the host side for immediate persistence
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def write_to_file(
        path: str, content: str, append: bool = False
    ) -> dict[str, Any]:
        """Write/append text to persistent storage with safe path handling."""
        if not append:
            return await memory_write(path=path, content=content)

        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    content,
                    append=True,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": saved_path,
                "chars": len(content),
                "mode": "append",
            }

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), mode="append", sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"path": resolved_path, "content": content},
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    def edit_core_memory(
        section: str,
        content: str,
        mode: str = "append",
    ) -> dict[str, Any]:
        """Edit core memory via append/replace operations with validation."""
        mode_norm = mode.strip().lower()
        if mode_norm not in {"append", "replace"}:
            return {
                "status": "error",
                "error": "mode must be one of: append, replace",
            }

        message = (
            ctx.agent.core_memory_append(section, content)
            if mode_norm == "append"
            else ctx.agent.core_memory_replace(section, content)
        )
        if message.startswith("Error:"):
            return {"status": "error", "error": message}

        return {
            "status": "ok",
            "section": section,
            "mode": mode_norm,
            "message": message,
            "chars": len(ctx.agent._core_memory.get(section, "")),
        }

    async def memory_list(path: str = ".") -> dict[str, Any]:
        """List files and directories in persistent storage."""
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        try:
            if path.strip() in {"", ".", "./"}:
                resolved_path = memory_root
            else:
                resolved_path, error = _resolve_path_or_error(
                    path=path,
                    default_root=memory_root,
                    allowed_root=allowed_root,
                )
                if error is not None:
                    return error
        except AttributeError:
            resolved_path = memory_root
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                items = await _adaytona_list_items(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "items": items,
                "count": len(items),
            }

        _reload_volume_best_effort(ctx)

        code = """
import os
try:
    items = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        kind = "dir" if os.path.isdir(full) else "file"
        items.append({"name": name, "type": kind})
    SUBMIT(status="ok", path=path, items=items, count=len(items))
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return await _aexecute_submit_ctx(ctx, code, variables={"path": resolved_path})

    async def run(command: str) -> dict[str, Any]:
        """Execute a bash command in the sandbox environment."""
        code = f"SUBMIT(result=run({command!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def workspace_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in the workspace directory."""
        code = f"SUBMIT(result=workspace_write({path!r}, {content!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def workspace_read(path: str) -> dict[str, Any]:
        """Read content from a file in the workspace directory."""
        code = f"SUBMIT(result=workspace_read({path!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def extract_python_ast(path: str) -> dict[str, Any]:
        """Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file"""
        code = f"SUBMIT(result=extract_python_ast({path!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def start_background_process(process_id: str, command: str) -> dict[str, Any]:
        """Start a non-blocking background process (daemon) in the sandbox."""
        code = f"SUBMIT(result=start_background_process({process_id!r}, {command!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def read_process_logs(process_id: str, tail: int = 50) -> dict[str, Any]:
        """Read the live stdout/stderr logs of an active background process."""
        code = f"SUBMIT(result=read_process_logs({process_id!r}, tail={tail}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def kill_process(process_id: str) -> dict[str, Any]:
        """Terminate a running background process by its ID."""
        code = f"SUBMIT(result=kill_process({process_id!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool

    # Sandbox editing tools
    tools.append(
        Tool(
            _sync_compatible_tool_callable(edit_file),
            name="edit_file",
            desc="Robustly edit a file by finding and replacing a unique text snippet",
        )
    )

    # Sandbox execution tools (Mapped from Interpreter Builtins)
    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(run),
                name="run",
                desc="Execute a bash command in the sandbox environment",
            ),
            Tool(
                _sync_compatible_tool_callable(workspace_write),
                name="workspace_write",
                desc="Write content to a file in the workspace directory",
            ),
            Tool(
                _sync_compatible_tool_callable(workspace_read),
                name="workspace_read",
                desc="Read content from a file in the workspace directory",
            ),
            Tool(
                _sync_compatible_tool_callable(extract_python_ast),
                name="extract_python_ast",
                desc="Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file",
            ),
            Tool(
                _sync_compatible_tool_callable(start_background_process),
                name="start_background_process",
                desc="Start a non-blocking background process (like a live webserver or watch compiler) by passing an arbitrary process ID and the shell command.",
            ),
            Tool(
                _sync_compatible_tool_callable(read_process_logs),
                name="read_process_logs",
                desc="Read the latest stdout/stderr logs of an active background process.",
            ),
            Tool(
                _sync_compatible_tool_callable(kill_process),
                name="kill_process",
                desc="Terminate a running background process.",
            ),
        ]
    )

    # Buffer & volume tools
    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(read_buffer),
                name="read_buffer",
                desc="Read the full contents of a sandbox buffer",
            ),
            Tool(
                _sync_compatible_tool_callable(clear_buffer),
                name="clear_buffer",
                desc="Clear one sandbox buffer (or all buffers when name is empty)",
            ),
            Tool(
                _sync_compatible_tool_callable(save_buffer_to_volume),
                name="save_buffer_to_volume",
                desc="Persist a sandbox buffer to persistent storage as JSON",
            ),
            Tool(
                _sync_compatible_tool_callable(load_text_from_volume),
                name="load_text_from_volume",
                desc="Load text from persistent storage into host-side document memory",
            ),
            Tool(
                _sync_compatible_tool_callable(process_document),
                name="process_document",
                desc="Load a document from persistent storage and register it for analysis",
            ),
        ]
    )

    # Persistent memory tools
    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(memory_read),
                name="memory_read",
                desc="Read a file from persistent storage",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_write),
                name="memory_write",
                desc="Write content to a file in persistent storage",
            ),
            Tool(
                _sync_compatible_tool_callable(write_to_file),
                name="write_to_file",
                desc="Write or append text to a file in persistent storage",
            ),
            Tool(
                edit_core_memory,
                name="edit_core_memory",
                desc="Edit core memory blocks using append or replace mode",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_list),
                name="memory_list",
                desc="List files and directories in persistent storage",
            ),
        ]
    )

    return tools
