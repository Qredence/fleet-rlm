"""Shared helpers for the DSPy ReAct tool registry."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

from dspy.primitives import FinalOutput

from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.runtime.execution.streaming import _normalize_trajectory
from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent


def normalize_strategy(strategy: str) -> str:
    """Normalise a chunking strategy name to its canonical form."""
    normalized = strategy.strip().lower().replace("-", "_")
    mapping = {
        "size": "size",
        "headers": "headers",
        "header": "headers",
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "json": "json_keys",
        "json_keys": "json_keys",
    }
    if normalized not in mapping:
        raise ValueError(
            "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
        )
    return mapping[normalized]


def chunk_text(
    text: str,
    strategy: str,
    *,
    size: int,
    overlap: int,
    pattern: str,
) -> list[Any]:
    """Chunk *text* using the named strategy."""
    strategy_norm = normalize_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=size, overlap=overlap)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def chunk_to_text(chunk: Any) -> str:
    """Convert a chunk to plain text."""
    if isinstance(chunk, str):
        return chunk
    if not isinstance(chunk, dict):
        return str(chunk)
    if "header" in chunk:
        return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
    if "timestamp" in chunk:
        return chunk.get("content", "")
    if "key" in chunk:
        return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()
    return json.dumps(chunk, ensure_ascii=False, default=str)


def resolve_document(agent: RLMReActChatAgent, alias: str) -> str:
    """Resolve a document alias to its full text content."""
    if alias == "active":
        if agent.active_alias is None:
            raise ValueError("No active document. Use load_document() first.")
        return agent._get_document(agent.active_alias)
    if alias not in agent._document_cache:
        raise ValueError(f"Unknown document alias: {alias}")
    return agent._get_document(alias)


def execute_submit(
    agent: RLMReActChatAgent,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
) -> dict[str, Any]:
    """Run *code* in the sandbox and return the SUBMIT() result."""
    # Tool executions must always use generic SUBMIT (**kwargs).  dspy.RLM
    # sets interpreter.output_fields before each forward() call, which
    # installs a typed SUBMIT(field1, field2, ...) in the sandbox context.
    # That typed SUBMIT rejects the extra keyword args used by sandbox tool
    # code (e.g. SUBMIT(status=..., result=...)).  Clear output_fields here
    # so aensure_setup restores the generic SUBMIT for this execution.
    interp = agent.interpreter
    if getattr(interp, "output_fields", None) is not None:
        interp.output_fields = None
    agent.start()
    result = agent.interpreter.execute(
        code,
        variables=variables or {},
        execution_profile=execution_profile,
    )
    return _normalize_submit_result(agent, result, variables=variables)


async def aexecute_submit(
    agent: RLMReActChatAgent,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
) -> dict[str, Any]:
    """Async variant of :func:`execute_submit` for loop-safe sandbox execution."""
    # Tool executions must always use generic SUBMIT (**kwargs).  dspy.RLM
    # sets interpreter.output_fields before each forward() call, which
    # installs a typed SUBMIT(field1, field2, ...) in the sandbox context.
    # That typed SUBMIT rejects the extra keyword args used by sandbox tool
    # code (e.g. SUBMIT(status=..., result=...)).  Clear output_fields here
    # so aensure_setup restores the generic SUBMIT for this execution.
    interp = agent.interpreter
    if getattr(interp, "output_fields", None) is not None:
        interp.output_fields = None
    await agent.astart()
    result = await _await_if_needed(
        agent.interpreter.aexecute(
            code,
            variables=variables or {},
            execution_profile=execution_profile,
        )
    )
    return _normalize_submit_result(agent, result, variables=variables)


async def _await_if_needed(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value


def _normalize_submit_result(
    agent: RLMReActChatAgent,
    result: Any,
    *,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize interpreter results into the stable SUBMIT payload contract."""
    if isinstance(result, FinalOutput):
        output = result.output
        if isinstance(output, dict):
            enriched = dict(output)
            enriched.setdefault("depth", agent.current_depth)
            parent_step_id = (variables or {}).get("parent_step_id")
            if (
                isinstance(parent_step_id, str)
                and parent_step_id
                and "parent_step_id" not in enriched
            ):
                enriched["parent_step_id"] = parent_step_id
            return enriched
        return {
            "output": output,
            "depth": agent.current_depth,
        }
    return {"output": str(result), "depth": agent.current_depth}


def build_trajectory_payload(
    source: Any, *, include_trajectory: bool
) -> dict[str, Any]:
    """Build a normalized trajectory payload from a prediction or raw dict."""
    if not include_trajectory:
        return {}

    def _coerce_trajectory(raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            return list(_normalize_trajectory(raw))
        if isinstance(raw, list):
            return list(raw)
        return []

    if isinstance(source, dict):
        raw = source.get("trajectory", [])
        trajectory = _coerce_trajectory(raw)
        final_reasoning = source.get("final_reasoning")
        depth = source.get("depth")
        parent_step_id = source.get("parent_step_id")
    else:
        trajectory = _coerce_trajectory(getattr(source, "trajectory", []))
        final_reasoning = getattr(source, "final_reasoning", None)
        depth = getattr(source, "depth", None)
        parent_step_id = getattr(source, "parent_step_id", None)

    payload: dict[str, Any] = {
        "trajectory_steps": len(trajectory),
        "trajectory": trajectory,
    }
    if final_reasoning:
        payload["final_reasoning"] = final_reasoning
    if isinstance(depth, (int, float)):
        payload["depth"] = int(depth)
    if isinstance(parent_step_id, str) and parent_step_id:
        payload["parent_step_id"] = parent_step_id
    return payload


_rlm_trajectory_payload = build_trajectory_payload


__all__ = [
    "aexecute_submit",
    "_rlm_trajectory_payload",
    "build_trajectory_payload",
    "chunk_text",
    "chunk_to_text",
    "execute_submit",
    "normalize_strategy",
    "resolve_document",
]
