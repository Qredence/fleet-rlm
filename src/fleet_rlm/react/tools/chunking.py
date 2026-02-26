"""DSPy ReAct chunking tools for the RLM chat agent.

This module contains chunking tools that allow the ReAct agent to split
documents into smaller pieces using various strategies. Tools are defined
as standalone functions following the DSPy convention.

Tools included:
- chunk_host: Chunk document on host using various strategies
- chunk_sandbox: Chunk active document inside sandbox and store chunks in a buffer
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import chunk_text, execute_submit, normalize_strategy, resolve_document

if TYPE_CHECKING:
    from ..agent import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_chunking_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build chunking tools with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.

    Args:
        agent: The RLMReActChatAgent instance to bind tools to.

    Returns:
        List of dspy.Tool objects for document chunking.
    """
    from dspy import Tool

    def chunk_host(
        strategy: str,
        alias: str = "active",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk document on host using size/headers/timestamps/json-keys strategies.

        Args:
            strategy: Chunking strategy: size, headers, timestamps, or json_keys.
            alias: Document alias to chunk. Defaults to active document.
            size: Chunk size for 'size' strategy. Defaults to 200000 characters.
            overlap: Overlap between chunks for 'size' strategy. Defaults to 0.
            pattern: Regex pattern for 'headers' or 'timestamps' strategies.

        Returns:
            Dictionary with status, strategy, chunk count, and preview.
        """
        text = resolve_document(agent, alias)
        chunks = chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
        preview = chunks[0] if chunks else ""
        return {
            "status": "ok",
            "strategy": strategy,
            "chunk_count": len(chunks),
            "preview": str(preview)[:400],
        }

    def chunk_sandbox(
        strategy: str,
        variable_name: str = "active_document",
        buffer_name: str = "chunks",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk the active document inside sandbox and store chunks in a buffer.

        Args:
            strategy: Chunking strategy: size, headers, timestamps, or json_keys.
            variable_name: Variable name for document in sandbox. Defaults to active_document.
            buffer_name: Buffer name to store chunks. Defaults to 'chunks'.
            size: Chunk size for 'size' strategy. Defaults to 200000 characters.
            overlap: Overlap between chunks for 'size' strategy. Defaults to 0.
            pattern: Regex pattern for 'headers' or 'timestamps' strategies.

        Returns:
            Dictionary with status, strategy, chunk count, and buffer name.
        """
        text = resolve_document(agent, "active")
        strategy_norm = normalize_strategy(strategy)

        code = """
import json

clear_buffer(buffer_name)

if strategy_norm == "size":
    chunks = chunk_by_size(active_document, size=size, overlap=overlap)
elif strategy_norm == "headers":
    chunks = chunk_by_headers(active_document, pattern=pattern or r"^#{1,3} ")
elif strategy_norm == "timestamps":
    chunks = chunk_by_timestamps(active_document, pattern=pattern or r"^\\d{4}-\\d{2}-\\d{2}[T ]")
elif strategy_norm == "json_keys":
    chunks = chunk_by_json_keys(active_document)
else:
    raise ValueError(f"Unsupported strategy: {strategy_norm}")

for chunk in chunks:
    add_buffer(buffer_name, chunk)

SUBMIT(
    status="ok",
    strategy=strategy_norm,
    chunk_count=len(chunks),
    buffer_name=buffer_name,
)
"""
        variables = {
            variable_name: text,
            "active_document": text,
            "strategy_norm": strategy_norm,
            "buffer_name": buffer_name,
            "size": size,
            "overlap": overlap,
            "pattern": pattern,
        }
        return execute_submit(agent, code, variables=variables)

    return [
        Tool(
            chunk_host,
            name="chunk_host",
            desc="Chunk document on host using size/headers/timestamps/json-keys strategies",
        ),
        Tool(
            chunk_sandbox,
            name="chunk_sandbox",
            desc="Chunk active document inside sandbox and store chunks in a buffer",
        ),
    ]
