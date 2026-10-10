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

    # The API can expose either AI SDK-compatible chunk names or Fleet's
    # canonical UI stream names. Both carry the same call id and payload.
    inputs = [
        chunk
        for chunk in chunks
        if chunk.get("type") in {"tool-input-available", "tool_call"} and chunk.get("toolName") == tool_name
    ]
    call_ids = {str(chunk.get("toolCallId")) for chunk in inputs}
    outputs = [
        chunk
        for chunk in chunks
        if chunk.get("type") in {"tool-output-available", "tool_result"}
        and str(chunk.get("toolCallId")) in call_ids
        and not chunk.get("error")
    ]
    errors = [
        chunk
        for chunk in chunks
        if (chunk.get("type") == "tool-output-error" or (chunk.get("type") == "tool_result" and chunk.get("error")))
        and str(chunk.get("toolCallId")) in call_ids
    ]
    return inputs, outputs, errors
