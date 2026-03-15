"""Fixed-size chunking strategy."""

from __future__ import annotations


def chunk_by_size(
    text: str,
    size: int = 200_000,
    overlap: int = 0,
) -> list[str]:
    """Split text into fixed-size chunks with optional overlap.

    Args:
        text: The text to split.
        size: Maximum characters per chunk. Default: 200,000.
        overlap: Number of overlapping characters between consecutive
            chunks. Default: 0.

    Returns:
        List of text chunks. Empty list if text is empty.

    Example:
        >>> chunks = chunk_by_size("abcdefghij", size=4, overlap=1)
        >>> chunks
        ['abcd', 'defg', 'ghij']
    """
    if not text:
        return []
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= size:
        raise ValueError("overlap must be less than size")

    chunks: list[str] = []
    step = size - overlap
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if chunk:
            chunks.append(chunk)
        # Stop if we've reached the end
        if start + size >= len(text):
            break
    return chunks
