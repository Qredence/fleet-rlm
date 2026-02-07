#!/usr/bin/env python3

"""
Semantic chunking for RLM workflow.
Splits content by semantic boundaries (headers, timestamps, etc.)
instead of fixed character counts.
"""

from __future__ import annotations

import argparse
import os
import pickle
import re
from pathlib import Path


def detect_content_type(content: str) -> str:
    """Auto-detect content type based on patterns."""
    # Check for markdown headers
    if re.search(r"^#{1,6} ", content, re.MULTILINE):
        return "markdown"

    # Check for log timestamps
    if re.search(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", content, re.MULTILINE):
        return "log"

    # Check for JSON
    if content.strip().startswith(("{", "[")):
        try:
            import json
            json.loads(content[:10000])  # Test first 10K
            return "json"
        except json.JSONDecodeError:
            pass

    # Check for code (Python)
    if re.search(r"^(def |class |import |from )", content, re.MULTILINE):
        return "python"

    return "text"


def chunk_markdown(content: str, max_size: int = 200000) -> list[tuple[int, int, str]]:
    """Chunk by markdown headers."""
    # Find all headers
    headers = list(re.finditer(r"^(#{1,6}) ", content, re.MULTILINE))

    if not headers:
        # Fall back to size-based
        return chunk_by_size(content, max_size)

    chunks = []
    for i, header in enumerate(headers):
        start = header.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)

        # If chunk is too big, split by sub-headers or size
        chunk_content = content[start:end]
        if len(chunk_content) > max_size:
            sub_chunks = chunk_by_size(chunk_content, max_size, start_offset=start)
            chunks.extend(sub_chunks)
        else:
            chunks.append((start, end, f"h{len(header.group(1))}"))

    return chunks


def chunk_logs(content: str, max_size: int = 200000) -> list[tuple[int, int, str]]:
    """Chunk by log entry timestamps."""
    # Common timestamp patterns
    patterns = [
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",  # ISO 8601
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",  # Common log format
        r"^\[\d{4}-\d{2}-\d{2}",  # Bracketed dates
        r"^\w{3} \d{1,2} \d{2}:\d{2}:\d{2}",  # Syslog format
    ]

    # Try each pattern
    timestamps = None
    for pattern in patterns:
        timestamps = list(re.finditer(pattern, content, re.MULTILINE))
        if len(timestamps) > 10:  # Need reasonable number of matches
            break

    if not timestamps or len(timestamps) < 10:
        return chunk_by_size(content, max_size)

    chunks = []
    for i, ts in enumerate(timestamps):
        start = ts.start()
        end = timestamps[i + 1].start() if i + 1 < len(timestamps) else len(content)

        chunk_content = content[start:end]
        if len(chunk_content) > max_size:
            sub_chunks = chunk_by_size(chunk_content, max_size, start_offset=start)
            chunks.extend(sub_chunks)
        else:
            chunks.append((start, end, "entry"))

    return chunks


def chunk_json(content: str, max_size: int = 200000) -> list[tuple[int, int, str]]:
    """Chunk by top-level JSON objects/arrays."""
    import json

    try:
        data = json.loads(content)

        if isinstance(data, list):
            # Split list into chunks
            items = data
            chunks = []
            current_start = 0
            current_items = []
            current_size = 0

            for item in items:
                item_str = json.dumps(item)
                item_size = len(item_str)

                if current_size + item_size > max_size and current_items:
                    # Save current chunk
                    chunk_content = json.dumps(current_items)
                    chunks.append((
                        content.find(chunk_content),
                        content.find(chunk_content) + len(chunk_content),
                        f"array[{len(current_items)}]"
                    ))
                    current_items = [item]
                    current_size = item_size
                else:
                    current_items.append(item)
                    current_size += item_size

            # Last chunk
            if current_items:
                chunk_content = json.dumps(current_items)
                chunks.append((
                    content.find(chunk_content),
                    content.find(chunk_content) + len(chunk_content),
                    f"array[{len(current_items)}]"
                ))

            return chunks

        elif isinstance(data, dict):
            # Split dict into key groups
            items = list(data.items())
            chunks = []
            current_keys = []
            current_size = 0

            for key, value in items:
                item_str = json.dumps({key: value})
                item_size = len(item_str)

                if current_size + item_size > max_size and current_keys:
                    chunk_dict = {k: data[k] for k in current_keys}
                    chunk_content = json.dumps(chunk_dict)
                    chunks.append((
                        content.find(chunk_content),
                        content.find(chunk_content) + len(chunk_content),
                        f"dict[{len(current_keys)}]"
                    ))
                    current_keys = [key]
                    current_size = item_size
                else:
                    current_keys.append(key)
                    current_size += item_size

            if current_keys:
                chunk_dict = {k: data[k] for k in current_keys}
                chunk_content = json.dumps(chunk_dict)
                chunks.append((
                    content.find(chunk_content),
                    content.find(chunk_content) + len(chunk_content),
                    f"dict[{len(current_keys)}]"
                ))

            return chunks

    except json.JSONDecodeError:
        pass

    return chunk_by_size(content, max_size)


def chunk_python(content: str, max_size: int = 200000) -> list[tuple[int, int, str]]:
    """Chunk by Python functions and classes."""
    # Find function and class definitions
    pattern = r"^(def |class )"
    definitions = list(re.finditer(pattern, content, re.MULTILINE))

    if len(definitions) < 5:
        return chunk_by_size(content, max_size)

    chunks = []
    for i, definition in enumerate(definitions):
        start = definition.start()
        end = definitions[i + 1].start() if i + 1 < len(definitions) else len(content)

        chunk_content = content[start:end]
        chunk_type = "class" if definition.group().startswith("class") else "function"

        if len(chunk_content) > max_size:
            sub_chunks = chunk_by_size(chunk_content, max_size, start_offset=start)
            chunks.extend(sub_chunks)
        else:
            chunks.append((start, end, chunk_type))

    return chunks


def chunk_by_size(
    content: str,
    size: int,
    overlap: int = 0,
    start_offset: int = 0,
) -> list[tuple[int, int, str]]:
    """Simple size-based chunking."""
    chunks = []
    step = size - overlap

    for i in range(0, len(content), step):
        start = i
        end = min(i + size, len(content))
        chunks.append((start + start_offset, end + start_offset, "size"))

        if end == len(content):
            break

    return chunks


def write_chunks(
    content: str,
    chunks: list[tuple[int, int, str]],
    output_dir: str,
    prefix: str = "chunk",
) -> list[str]:
    """Write chunks to files."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    for i, (start, end, chunk_type) in enumerate(chunks):
        chunk_content = content[start:end]
        filename = f"{prefix}_{i:04d}_{chunk_type}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w") as f:
            f.write(f"<!-- Chunk {i}: bytes {start}-{end}, type={chunk_type} -->\n")
            f.write(chunk_content)

        paths.append(filepath)

    return paths


def main():
    parser = argparse.ArgumentParser(
        description="Create semantic chunks from content"
    )
    parser.add_argument(
        "--state",
        default=".claude/rlm_state/state.pkl",
        help="Path to RLM state file",
    )
    parser.add_argument(
        "--type",
        choices=["auto", "markdown", "log", "json", "python", "text"],
        default="auto",
        help="Content type (auto-detect if not specified)",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=200000,
        help="Maximum chunk size in characters",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=0,
        help="Overlap between chunks in characters",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=".claude/rlm_state/chunks",
        help="Output directory for chunks",
    )
    parser.add_argument(
        "--prefix",
        default="chunk",
        help="Filename prefix for chunks",
    )

    args = parser.parse_args()

    # Load content
    with open(args.state, "rb") as f:
        state = pickle.load(f)
    content = state.get("content", "")

    # Detect content type
    content_type = args.type
    if content_type == "auto":
        content_type = detect_content_type(content)
        print(f"Auto-detected content type: {content_type}")

    # Chunk based on type
    chunkers = {
        "markdown": chunk_markdown,
        "log": chunk_logs,
        "json": chunk_json,
        "python": chunk_python,
        "text": chunk_by_size,
    }

    chunker = chunkers.get(content_type, chunk_by_size)

    if content_type == "text":
        chunks = chunker(content, args.max_size, args.overlap)
    else:
        chunks = chunker(content, args.max_size)

    # Write chunks
    paths = write_chunks(content, chunks, args.output, args.prefix)

    print(f"Created {len(paths)} semantic chunks in {args.output}")
    print(f"\nChunk breakdown:")
    type_counts = {}
    for _, _, ct in chunks:
        type_counts[ct] = type_counts.get(ct, 0) + 1
    for ct, count in sorted(type_counts.items()):
        print(f"  {ct}: {count}")


if __name__ == "__main__":
    main()
