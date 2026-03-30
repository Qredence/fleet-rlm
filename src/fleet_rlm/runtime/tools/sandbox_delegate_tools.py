"""Delegate and cached-runtime tool builders for sandbox runtimes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.signatures import GroundedCitation
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .runtime_module_helpers import coerce_int as _coerce_int
from .runtime_module_helpers import coerce_str_list as _coerce_str_list
from .runtime_module_helpers import prediction_value as _prediction_value
from .runtime_module_helpers import run_cached_runtime_module as _run_runtime_module
from .runtime_module_helpers import runtime_metadata as _runtime_metadata
from .sandbox_common import _aexecute_submit_ctx, _SandboxToolContext
from .shared import (
    build_trajectory_payload,
    chunk_text,
    chunk_to_text,
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
    sandbox_ctx = _SandboxToolContext(agent=agent)

    try:
        from fleet_rlm.integrations.providers.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:  # pragma: no cover - defensive import guard
        DaytonaInterpreter = None  # type: ignore[assignment]

    def _supports_daytona_recursive_batching() -> bool:
        return bool(
            DaytonaInterpreter is not None
            and isinstance(ctx.agent.interpreter, DaytonaInterpreter)
        )

    def _effective_recursive_batch_concurrency(task_count: int) -> int:
        configured = getattr(ctx.agent, "daytona_batch_concurrency", None)
        try:
            value = int(configured) if configured is not None else 0
        except (TypeError, ValueError):
            value = 0

        if value <= 0:
            delegate_limit = max(
                1, int(getattr(ctx.agent, "delegate_max_calls_per_turn", 1))
            )
            value = min(delegate_limit, 4)

        delegate_limit = max(
            1, int(getattr(ctx.agent, "delegate_max_calls_per_turn", 1))
        )
        return max(1, min(value, delegate_limit, max(1, int(task_count))))

    def _normalize_batched_task(
        task: Any, index: int
    ) -> tuple[str, str] | dict[str, Any]:
        if not isinstance(task, dict):
            return {
                "index": index,
                "callback_name": "rlm_query_batched",
                "status": "error",
                "error": "Each task must be an object with query and optional context.",
            }

        query = str(task.get("query", "") or "").strip()
        context = str(task.get("context", "") or "")
        if not query:
            return {
                "index": index,
                "callback_name": "rlm_query_batched",
                "status": "error",
                "error": "Task query cannot be empty.",
            }
        return query, context

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
        return await _aexecute_submit_ctx(
            sandbox_ctx,
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

    async def rlm_query_batched(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if not _supports_daytona_recursive_batching():
            return {
                "status": "error",
                "error": "rlm_query_batched is only available in the Daytona runtime.",
            }

        if not isinstance(tasks, list) or not tasks:
            return {
                "status": "error",
                "error": "tasks must be a non-empty list of {query, context} objects.",
            }

        results: list[dict[str, Any] | None] = [None] * len(tasks)
        semaphore = asyncio.Semaphore(
            _effective_recursive_batch_concurrency(len(tasks))
        )

        async def _run_one(index: int, raw_task: Any) -> None:
            normalized = _normalize_batched_task(raw_task, index)
            if isinstance(normalized, dict):
                results[index] = normalized
                return

            query, context = normalized
            async with semaphore:
                result = await spawn_delegate_sub_agent_async(
                    ctx.agent,
                    prompt=query,
                    context=context,
                    stream_event_callback=getattr(
                        ctx.agent, "_live_event_callback", None
                    ),
                )

            if result.get("status") == "error":
                results[index] = {
                    "index": index,
                    "query": query,
                    "context": context,
                    "callback_name": "rlm_query_batched",
                    "status": "error",
                    "error": str(
                        result.get("error", "Unknown recursive batching error.")
                    ),
                }
                return

            results[index] = {
                "index": index,
                "query": query,
                "context": context,
                "callback_name": "rlm_query_batched",
                "status": "ok",
                "answer": result.get("answer") or result.get("assistant_response", ""),
                "sub_agent_history": result.get("sub_agent_history", 0),
                "depth": result.get(
                    "depth", getattr(ctx.agent, "_current_depth", 0) + 1
                ),
                **build_trajectory_payload(result, include_trajectory=True),
            }

        await asyncio.gather(
            *(_run_one(index, raw_task) for index, raw_task in enumerate(tasks))
        )

        ordered_results = [item for item in results if isinstance(item, dict)]
        success_count = sum(1 for item in ordered_results if item.get("status") == "ok")

        return {
            "status": "ok",
            "callback_name": "rlm_query_batched",
            "task_count": len(tasks),
            "success_count": success_count,
            "error_count": len(tasks) - success_count,
            "batch_concurrency": _effective_recursive_batch_concurrency(len(tasks)),
            "results": ordered_results,
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

    if not _supports_daytona_recursive_batching():
        tools.insert(
            0,
            _build_tool(
                _ToolRegistration(
                    name="parallel_semantic_map",
                    desc="Run batched semantic analysis over document chunks and store findings in a buffer",
                    func=parallel_semantic_map,
                )
            ),
        )

    if _supports_daytona_recursive_batching():
        tools.append(
            _build_tool(
                _ToolRegistration(
                    name="rlm_query_batched",
                    desc="Run multiple bounded recursive sub-agent queries in parallel and return ordered per-task results",
                    func=rlm_query_batched,
                )
            )
        )

    return tools
