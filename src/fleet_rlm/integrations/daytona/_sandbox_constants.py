"""Shared constants used by sandbox_executor and bridge_callbacks."""

from __future__ import annotations

_FINAL_OUTPUT_MARKER = "__DSPY_FINAL_OUTPUT__"
_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES: frozenset[str] = frozenset({"run", "workspace_write", "workspace_read"})
# Agent-level tools that must NOT be called from inside sandbox code.
# Note: sub_rlm / sub_rlm_batched ARE allowed — they are the true-RLM
# recursive primitives bridged via the HTTP broker.
_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS: tuple[str, ...] = (
    "rlm_query",
    "rlm_query_batched",
)

__all__ = [
    "_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES",
    "_FINAL_OUTPUT_MARKER",
    "_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS",
]
