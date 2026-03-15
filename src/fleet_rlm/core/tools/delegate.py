"""RLM delegation tools for sandbox-based agent operations.

Heavy long-context tools in this module use cached ``dspy.RLM`` runtime
modules (via ``agent.get_runtime_module``) instead of spawning recursive
sub-agents per call. ``rlm_query`` remains the explicit true-recursion
escape hatch for advanced delegation.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.core.agent.rlm_agent import spawn_delegate_sub_agent_async
from fleet_rlm.core.agent.tool_delegation import _sync_compatible_tool_callable

from . import (
    build_trajectory_payload,
    chunk_text,
    chunk_to_text,
    execute_submit,
    resolve_document,
)

if TYPE_CHECKING:
    from ..chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _DelegateToolContext:
    """Shared context for RLM delegate tool callables."""

    agent: "RLMReActChatAgent"


def _coerce_int(
    value: Any,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _prediction_value(prediction: Any, field_name: str, default: Any) -> Any:
    if isinstance(prediction, dict):
        return prediction.get(field_name, default)
    return getattr(prediction, field_name, default)


def _claim_delegate_slot_or_error(agent: "RLMReActChatAgent") -> dict[str, Any] | None:
    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                "Cannot run delegate operation."
            ),
        }

    claim_slot = getattr(agent, "_claim_delegate_slot", None)
    if not callable(claim_slot):
        return None

    claim_result = claim_slot()
    if (
        isinstance(claim_result, tuple)
        and len(claim_result) == 2
        and isinstance(claim_result[0], bool)
    ):
        allowed = bool(claim_result[0])
        limit = _coerce_int(claim_result[1], default=1, minimum=1)
        if not allowed:
            return {
                "status": "error",
                "error": (
                    "Delegate call budget reached for this turn. "
                    f"Maximum delegate calls per turn is {limit}."
                ),
                "delegate_max_calls_per_turn": limit,
            }

    return None


def _run_runtime_module(
    ctx: _DelegateToolContext,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any | None, dict[str, Any] | None, bool]:
    guard_error = _claim_delegate_slot_or_error(ctx.agent)
    if guard_error is not None:
        return None, guard_error, False

    ctx.agent.start()

    try:
        module = ctx.agent.get_runtime_module(module_name)
    except Exception as exc:
        return (
            None,
            {
                "status": "error",
                "error": (
                    f"Failed to load runtime module '{module_name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            False,
        )

    delegate_lm = getattr(ctx.agent, "delegate_lm", None)
    parent_lm = getattr(dspy.settings, "lm", None)

    fallback_used = False
    if delegate_lm is None:
        fallback_used = True
        record_fallback = getattr(ctx.agent, "_record_delegate_fallback", None)
        if callable(record_fallback):
            record_fallback()

    try:
        if delegate_lm is not None:
            lm_context = dspy.context(lm=delegate_lm)
        elif parent_lm is not None:
            lm_context = dspy.context(lm=parent_lm)
        else:
            lm_context = nullcontext()

        with lm_context:
            prediction = module(**kwargs)
    except Exception as exc:
        if delegate_lm is not None and parent_lm is not None:
            record_fallback = getattr(ctx.agent, "_record_delegate_fallback", None)
            if callable(record_fallback):
                record_fallback()
            fallback_used = True
            with dspy.context(lm=parent_lm):
                prediction = module(**kwargs)
        else:
            return (
                None,
                {
                    "status": "error",
                    "error": (
                        f"Runtime module '{module_name}' failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                fallback_used,
            )

    return prediction, None, fallback_used


def _runtime_metadata(
    ctx: _DelegateToolContext,
    prediction: Any,
    *,
    fallback_used: bool,
) -> dict[str, Any]:
    return {
        "depth": _coerce_int(
            _prediction_value(prediction, "depth", ctx.agent._current_depth + 1),
            default=ctx.agent._current_depth + 1,
            minimum=0,
        ),
        "sub_agent_history": _coerce_int(
            _prediction_value(prediction, "sub_agent_history", 0),
            default=0,
            minimum=0,
        ),
        "delegate_lm_fallback": bool(fallback_used),
    }


def build_rlm_delegate_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build RLM delegation tools bound to *agent*."""
    ctx = _DelegateToolContext(agent=agent)

    def parallel_semantic_map(
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
        chunk_texts = [chunk_to_text(c) for c in chunks][:max_chunks]

        prompts = []
        for idx, chunk_item in enumerate(chunk_texts):
            prompts.append(
                f"Query: {query}\nChunk index: {idx}\nReturn concise findings as plain text.\n\n{chunk_item[:6000]}"
            )

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
        return execute_submit(
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
        """Analyze a long document via the runtime RLM module."""
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def summarize_long_document(
        focus: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        """Summarize a long document via the runtime RLM module."""
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def extract_from_logs(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        """Extract structured patterns from log text via the runtime RLM module."""
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
            "extract_from_logs",
            logs=document,
            query=query,
        )
        if error is not None:
            return error

        raw_patterns = _prediction_value(prediction, "patterns", {})
        patterns: dict[str, str] | list[str]
        if isinstance(raw_patterns, dict):
            patterns = {str(key): str(value) for key, value in raw_patterns.items()}
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
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
        """Answer a query with explicit machine-readable citations."""
        max_chunks_int = _coerce_int(max_chunks, default=-1)
        if max_chunks_int <= 0:
            return {"status": "error", "error": "Invalid max_chunks value."}

        document = resolve_document(ctx.agent, alias)
        chunks = chunk_text(
            document, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        evidence_chunks = [chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return {"status": "error", "error": "No evidence chunks available."}

        prediction, error, fallback_used = _run_runtime_module(
            ctx,
            "grounded_answer",
            query=query,
            evidence_chunks=evidence_chunks,
            response_style="concise",
        )
        if error is not None:
            return error

        raw_citations = _prediction_value(prediction, "citations", [])
        citations: list[dict[str, Any]] = []
        if isinstance(raw_citations, list):
            for item in raw_citations:
                if isinstance(item, dict):
                    citations.append(dict(item))

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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction,
                include_trajectory=include_trajectory,
            ),
        }

    async def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Triage logs into severity, causes, impact, and actions."""
        document = resolve_document(ctx.agent, alias)
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
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
        """Build a structured code-change plan via runtime RLM module."""
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Propose safe updates to core memory via runtime RLM module."""
        turn_lines = [
            f"Turn {i}\n{turn}"
            for i, turn in enumerate(ctx.agent.history_messages()[-20:], 1)
        ]
        turn_history = "\n\n".join(turn_lines) or "No recent turns."
        prediction, error, fallback_used = _run_runtime_module(
            ctx,
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
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
            **build_trajectory_payload(
                prediction, include_trajectory=include_trajectory
            ),
        }

    async def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        """Use bounded child-RLM recursion for deep symbolic work.

        Reach for this tool when the task needs multi-step reasoning in a
        Python REPL, deeper code or repo exploration, program synthesis, or
        other symbolic analysis that is heavier than normal conversational
        tool use.
        """
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
        Tool(parallel_semantic_map, name="parallel_semantic_map"),
        Tool(
            _sync_compatible_tool_callable(analyze_long_document),
            name="analyze_long_document",
        ),
        Tool(
            _sync_compatible_tool_callable(summarize_long_document),
            name="summarize_long_document",
        ),
        Tool(
            _sync_compatible_tool_callable(extract_from_logs), name="extract_from_logs"
        ),
        Tool(_sync_compatible_tool_callable(grounded_answer), name="grounded_answer"),
        Tool(
            _sync_compatible_tool_callable(triage_incident_logs),
            name="triage_incident_logs",
        ),
        Tool(_sync_compatible_tool_callable(plan_code_change), name="plan_code_change"),
        Tool(
            _sync_compatible_tool_callable(propose_core_memory_update),
            name="propose_core_memory_update",
        ),
        Tool(_sync_compatible_tool_callable(rlm_query), name="rlm_query"),
    ]
