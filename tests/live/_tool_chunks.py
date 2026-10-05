"""Tool-pair assertions shared by live memory scenarios."""

from typing import Any


def _tool_chunks(chunks: list[dict[str, Any]], tool_name: str, chunk_type: str) -> list[dict[str, Any]]:
    return [chunk for chunk in chunks if chunk.get("type") == chunk_type and chunk.get("toolName") == tool_name]


def _paired_tool_chunks(
    chunks: list[dict[str, Any]], tool_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair one tool's input chunks with their output/error chunks by toolCallId.

    The SSE projection carries ``toolName`` only on ``tool-input-available`` frames
    (matching both the in-repo ``ToolOutputAvailable`` model and the AI SDK stream
    protocol); outputs pair to their tool strictly through ``toolCallId``.
    """

    inputs = _tool_chunks(chunks, tool_name, "tool-input-available")
    call_ids = {str(chunk.get("toolCallId")) for chunk in inputs}
    outputs = [
        chunk
        for chunk in chunks
        if chunk.get("type") == "tool-output-available" and str(chunk.get("toolCallId")) in call_ids
    ]
    errors = [
        chunk
        for chunk in chunks
        if chunk.get("type") == "tool-output-error" and str(chunk.get("toolCallId")) in call_ids
    ]
    return inputs, outputs, errors
