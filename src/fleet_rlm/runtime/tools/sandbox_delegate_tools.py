"""Delegate and cached-runtime tool builders for sandbox runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.signatures import GroundedCitation
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .runtime_module_helpers import coerce_int as _coerce_int
from .runtime_module_helpers import coerce_str_list as _coerce_str_list
from .runtime_module_helpers import prediction_value as _prediction_value
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


def _run_runtime_module_via_sandbox(*args: Any, **kwargs: Any):
    sandbox_module = import_module("fleet_rlm.runtime.tools.sandbox")
    return sandbox_module._run_runtime_module(*args, **kwargs)


def build_rlm_delegate_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build cached-runtime and recursive delegation tools bound to *agent*."""
    from dspy import Tool

    ctx = _DelegateToolContext(agent=agent)
    sandbox_ctx = _SandboxToolContext(agent=agent)

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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
        prediction, error, fallback_used = _run_runtime_module_via_sandbox(
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
            desc="Extract structured matches and patterns from a loaded log document",
        ),
        Tool(
            _sync_compatible_tool_callable(grounded_answer),
            name="grounded_answer",
            desc="Answer a question with grounded citations from a loaded document",
        ),
        Tool(
            _sync_compatible_tool_callable(triage_incident_logs),
            name="triage_incident_logs",
            desc="Triage incident logs and suggest likely causes and recommended actions",
        ),
        Tool(
            _sync_compatible_tool_callable(plan_code_change),
            name="plan_code_change",
            desc="Produce a code-change plan with files, validation commands, and risks",
        ),
        Tool(
            _sync_compatible_tool_callable(propose_core_memory_update),
            name="propose_core_memory_update",
            desc="Suggest keep/update/remove actions for core memory after recent conversation turns",
        ),
        Tool(
            _sync_compatible_tool_callable(rlm_query),
            name="rlm_query",
            desc="Run a bounded recursive sub-agent query in a fresh child runtime and return the answer",
        ),
    ]
