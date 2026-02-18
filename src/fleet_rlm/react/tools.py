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
from typing import TYPE_CHECKING, Any, Callable, Iterable

from dspy.primitives.code_interpreter import FinalOutput

from ..chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from ..core.interpreter import ExecutionProfile

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


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
            return output
        return {"output": output}
    return {"output": str(result)}


def _rlm_trajectory_payload(result: Any, *, include_trajectory: bool) -> dict[str, Any]:
    """Build a normalized trajectory payload from a DSPy RLM result."""
    if not include_trajectory:
        return {}

    trajectory = list(getattr(result, "trajectory", []) or [])
    payload: dict[str, Any] = {
        "trajectory_steps": len(trajectory),
        "trajectory": trajectory,
    }
    final_reasoning = getattr(result, "final_reasoning", None)
    if final_reasoning:
        payload["final_reasoning"] = final_reasoning
    return payload


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

    from .chunking_tools import build_chunking_tools
    from .document_tools import build_document_tools
    from .filesystem_tools import build_filesystem_tools
    from .tools_sandbox import build_sandbox_tools

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
    return tools


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
