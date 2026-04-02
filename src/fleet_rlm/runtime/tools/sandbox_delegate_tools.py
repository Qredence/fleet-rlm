"""Delegate and cached-runtime tool builders for sandbox runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.signatures import GroundedCitation
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .llm_tools import coerce_int as _coerce_int
from .llm_tools import coerce_str_list as _coerce_str_list
from .llm_tools import prediction_value as _prediction_value
from .llm_tools import run_cached_runtime_module as _run_runtime_module
from .llm_tools import runtime_metadata as _runtime_metadata
from .shared import (
    build_trajectory_payload,
    resolve_document,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


@dataclass(slots=True)
class _DelegateToolContext:
    """Shared context for RLM delegation tool callables."""

    agent: RLMReActChatAgent


@dataclass(frozen=True, slots=True)
class _ToolRegistration:
    """Compact record for building DSPy tools from local callables."""

    name: str
    desc: str
    func: Any


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


def _run_cached_runtime_module(
    ctx: _DelegateToolContext,
    *,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any] | None, bool]:
    return _run_runtime_module(
        ctx.agent,
        module_name,
        **kwargs,
    )


def _cached_runtime_success(
    ctx: _DelegateToolContext,
    *,
    prediction: Any,
    fallback_used: bool,
    include_trajectory: bool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "ok",
        **payload,
        **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        **build_trajectory_payload(prediction, include_trajectory=include_trajectory),
    }


def _record_runtime_failure(
    ctx: _DelegateToolContext,
    error: dict[str, Any] | None,
) -> None:
    if not isinstance(error, dict):
        return
    category = str(error.get("runtime_failure_category", "") or "").strip() or None
    phase = str(error.get("runtime_failure_phase", "") or "").strip() or None
    if category is None and phase is None:
        return
    recorder = getattr(ctx.agent.interpreter, "mark_runtime_degradation", None)
    if callable(recorder):
        recorder(category=category, phase=phase, fallback_used=False)


def _build_tool(registration: _ToolRegistration) -> Any:
    from dspy import Tool

    return Tool(
        _sync_compatible_tool_callable(registration.func),
        name=registration.name,
        desc=registration.desc,
    )


def build_rlm_delegate_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build cached-runtime and recursive delegation tools bound to *agent*."""
    ctx = _DelegateToolContext(agent=agent)

    async def analyze_long_document(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="analyze_long_document",
            document=document,
            query=query,
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "findings": _coerce_str_list(
                    _prediction_value(prediction, "findings", [])
                ),
                "answer": str(_prediction_value(prediction, "answer", "")),
                "sections_examined": _coerce_int(
                    _prediction_value(prediction, "sections_examined", 0),
                    default=0,
                    minimum=0,
                ),
                "doc_chars": len(document),
            },
        )

    async def summarize_long_document(
        focus: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="summarize_long_document",
            document=document,
            focus=focus,
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
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
            },
        )

    async def extract_from_logs(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="extract_from_logs",
            logs=document,
            query=query,
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
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

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "matches": _coerce_str_list(
                    _prediction_value(prediction, "matches", [])
                ),
                "patterns": patterns,
                "time_range": str(
                    _prediction_value(prediction, "time_range", "unknown")
                ),
                "doc_chars": len(document),
            },
        )

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
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="grounded_answer",
            document=document,
            query=query,
            chunk_strategy=chunk_strategy,
            max_chunks=max_chunks_int,
            response_style="concise",
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        citations = _normalize_grounded_citations(
            _prediction_value(prediction, "citations", [])
        )

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "answer": str(_prediction_value(prediction, "answer", "")),
                "citations": citations,
                "confidence": _coerce_int(
                    _prediction_value(prediction, "confidence", 0),
                    default=0,
                    minimum=0,
                    maximum=100,
                ),
                "coverage_notes": str(
                    _prediction_value(prediction, "coverage_notes", "")
                ),
                "doc_chars": len(document),
            },
        )

    async def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="triage_incident_logs",
            logs=document,
            service_context=service_context,
            query=query,
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        severity = str(_prediction_value(prediction, "severity", "low")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "low"

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
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
                "time_range": str(
                    _prediction_value(prediction, "time_range", "unknown")
                ),
            },
        )

    async def plan_code_change(
        task: str,
        repo_context: str = "",
        constraints: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="plan_code_change",
            task=task,
            repo_context=repo_context,
            constraints=constraints or "Keep changes minimal.",
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
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
            },
        )

    async def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        turn_lines = [
            f"Turn {idx}\n{turn}"
            for idx, turn in enumerate(ctx.agent.history_messages()[-20:], 1)
        ]
        turn_history = "\n\n".join(turn_lines) or "No recent turns."
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="propose_core_memory_update",
            turn_history=turn_history,
            current_memory=ctx.agent.fmt_core_memory(),
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "keep": _coerce_str_list(_prediction_value(prediction, "keep", [])),
                "update": _coerce_str_list(_prediction_value(prediction, "update", [])),
                "remove": _coerce_str_list(_prediction_value(prediction, "remove", [])),
                "rationale": str(_prediction_value(prediction, "rationale", "")),
            },
        )

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

    registrations = [
        _ToolRegistration(
            name="analyze_long_document",
            desc="Analyze a long document with the cached runtime module and return findings plus an answer",
            func=analyze_long_document,
        ),
        _ToolRegistration(
            name="summarize_long_document",
            desc="Summarize a long document with key points and coverage metadata",
            func=summarize_long_document,
        ),
        _ToolRegistration(
            name="extract_from_logs",
            desc="Extract structured matches and patterns from a loaded log document",
            func=extract_from_logs,
        ),
        _ToolRegistration(
            name="grounded_answer",
            desc="Answer a question with grounded citations from a loaded document",
            func=grounded_answer,
        ),
        _ToolRegistration(
            name="triage_incident_logs",
            desc="Triage incident logs and suggest likely causes and recommended actions",
            func=triage_incident_logs,
        ),
        _ToolRegistration(
            name="plan_code_change",
            desc="Produce a code-change plan with files, validation commands, and risks",
            func=plan_code_change,
        ),
        _ToolRegistration(
            name="propose_core_memory_update",
            desc="Suggest keep/update/remove actions for core memory after recent conversation turns",
            func=propose_core_memory_update,
        ),
        _ToolRegistration(
            name="rlm_query",
            desc="Run a bounded recursive sub-agent query in a fresh child runtime and return the answer",
            func=rlm_query,
        ),
    ]
    tools: list[Any] = [_build_tool(registration) for registration in registrations]

    # Batch tools (parallel_semantic_map, rlm_query_batched) from batch_tools
    from .batch_tools import build_batch_tools

    batch_prepend, batch_append = build_batch_tools(agent)
    tools = batch_prepend + tools + batch_append

    return tools
