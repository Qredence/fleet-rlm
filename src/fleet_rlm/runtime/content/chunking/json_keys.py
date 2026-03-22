"""JSON key-based chunking strategy."""

from __future__ import annotations

import json


def chunk_by_json_keys(text: str) -> list[dict]:
    """Split a JSON object into per-key chunks.

    Parses the text as JSON and creates one chunk per top-level key.
    Useful for exploring large JSON configurations or API responses.

    Args:
        text: JSON string to split. Must be a JSON object (dict) at
            the top level.

    Returns:
        List of dicts with keys:
            - ``key``: The top-level JSON key
            - ``content``: JSON-serialized value for that key
            - ``value_type``: Python type name of the value

    Raises:
        ValueError: If text is not valid JSON or not a JSON object.

    Example:
        >>> text = '{"users": [1,2], "config": {"debug": true}}'
        >>> chunks = chunk_by_json_keys(text)
        >>> chunks[0]["key"]
        'users'
    """
    if not text or not text.strip():
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    chunks: list[dict] = []
    for key, value in data.items():
        chunks.append(
            {
                "key": key,
                "content": json.dumps(value, indent=2, default=str),
                "value_type": type(value).__name__,
            }
        )

    return chunks
