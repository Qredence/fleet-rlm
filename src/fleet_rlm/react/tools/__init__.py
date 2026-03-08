"""DSPy ReAct tool definitions for the RLM chat agent.

Tools are defined as standalone functions following the DSPy convention:
each tool has a docstring (for the LLM description), type-hinted parameters
(for JSON schema generation), and returns ``dict[str, Any]``.

The :func:`build_tool_list` factory creates closures that capture the agent
instance, so ``dspy.ReAct`` only sees clean user-facing signatures — no
``self`` parameter.

See: https://dspy.ai/tutorials/customer_service_agent/#define-tools
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal

from dspy.primitives.code_interpreter import FinalOutput

from ...chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from ...core.interpreter import ExecutionProfile
from ..streaming_citations import _normalize_trajectory

if TYPE_CHECKING:
    from ..agent import RLMReActChatAgent

logger = logging.getLogger(__name__)

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]

_RLM_DELEGATION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "parallel_semantic_map",
        "rlm_query",
        "analyze_long_document",
        "summarize_long_document",
        "extract_from_logs",
        "grounded_answer",
        "triage_incident_logs",
        "plan_code_change",
        "propose_core_memory_update",
    }
)

_MEMORY_INTELLIGENCE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
    }
)

_RLM_HEAVY_TOOL_NAMES: frozenset[str] = (
    _RLM_DELEGATION_TOOL_NAMES | _MEMORY_INTELLIGENCE_TOOL_NAMES
)

_RLM_ONLY_TOOL_NAMES: frozenset[str] = frozenset({"rlm_query"})


# ---------------------------------------------------------------------------
# Shared helpers (used by multiple tools, not exposed to DSPy)
# ---------------------------------------------------------------------------


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
    """Convert a chunk to plain text.

    Uses a lookup-based approach instead of multiple ``isinstance`` checks
    for better performance with large document collections.
    """
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
    agent.start()
    result = agent.interpreter.execute(
        code,
        variables=variables or {},
        execution_profile=execution_profile,
    )
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
    """Build a normalised trajectory payload from either a ``dspy.Prediction``
    object or a raw sub-agent response ``dict``.

    Both callsites (``tools_rlm_delegate.py`` and inline tool helpers) use this
    single function so the trajectory key schema stays consistent.

    Returns an empty dict when *include_trajectory* is False.
    """
    if not include_trajectory:
        return {}

    def _coerce_trajectory(raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            return list(_normalize_trajectory(raw))
        if isinstance(raw, list):
            return list(raw)
        return []

    # Accept both Prediction objects (getattr) and plain dicts
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


# Back-compat alias so existing callers of the private name keep working.
_rlm_trajectory_payload = build_trajectory_payload


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_tool_list(
    agent: RLMReActChatAgent,
    extra_tools: list[Callable[..., Any]] | None = None,
) -> list[Any]:
    """Build the DSPy ReAct tool list with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.

    Tools are organized by category and imported from dedicated modules:
    - Document tools: load_document, set_active_document, list_documents
    - Filesystem tools: list_files, read_file_slice, find_files
    - Chunking tools: chunk_host, chunk_sandbox
    - Sandbox tools: RLM delegation, memory, buffer, volume operations
    """
    from dspy import Tool

    from .chunking import build_chunking_tools
    from .document import build_document_tools
    from .filesystem import build_filesystem_tools
    from .sandbox import build_sandbox_tools

    tools: list[Tool] = []

    # Document management tools (load, set_active, list)
    tools.extend(build_document_tools(agent))

    # Filesystem navigation tools (list, read_slice, find)
    tools.extend(build_filesystem_tools(agent))

    # Chunking tools (host and sandbox)
    tools.extend(build_chunking_tools(agent))

    # Sandbox / RLM / buffer / volume tools
    tools.extend(build_sandbox_tools(agent))

    # Wrap extra tools with dspy.Tool if not already wrapped
    if extra_tools:
        for et in extra_tools:
            if isinstance(et, Tool):
                tools.append(et)
            else:
                tools.append(Tool(et))

    return _filter_tools_for_execution_mode(
        tools,
        getattr(agent, "execution_mode", "auto"),
    )


def _filter_tools_for_execution_mode(
    tools: list[Any], execution_mode: ExecutionMode | str
) -> list[Any]:
    """Return the subset of *tools* allowed for the selected execution mode."""
    if execution_mode == "tools_only":
        return [tool for tool in tools if _tool_name(tool) not in _RLM_HEAVY_TOOL_NAMES]

    if execution_mode == "rlm_only":
        return [tool for tool in tools if _tool_name(tool) in _RLM_ONLY_TOOL_NAMES]

    return tools


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def list_react_tool_names(tools: Iterable[Any]) -> list[str]:
    """Return stable tool names for display / debugging.

    Handles both raw callables (``__name__``) and ``dspy.Tool`` wrappers
    (``.name``).
    """
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        names.append(name)
    return names
