#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pickle
import re
from pathlib import Path

DEFAULT_STATE_PATH = ".codex/rlm_state/state.pkl"
DEFAULT_CHUNKS_DIR = ".codex/rlm_state/chunks"


def load_content(state_path: str):
    with open(state_path, "rb") as handle:
        state = pickle.load(handle)  # nosec
    return state.get("content", "")


def detect_content_type(content: str):
    text = content.lstrip()
    probes = (
        ("json", text[:1] in "{["),
        ("markdown", text.startswith("#") or "\n#" in content),
        ("python", "def " in content),
        ("log", re.search(r"^\d{4}-\d{2}-\d{2}", content, re.MULTILINE) is not None),
    )
    return next((name for name, matched in probes if matched), "text")


def size_chunks(content: str, size: int, overlap: int = 0, offset: int = 0):
    limit = max(1, size)
    stride = max(1, limit - max(0, overlap))
    chunks = []
    start = 0
    while start < len(content):
        end = min(start + limit, len(content))
        chunks.append((offset + start, offset + end, content[start:end]))
        start += stride
    if chunks:
        return chunks
    return [(offset, offset, "")]


def regex_chunks(content: str, pattern: str, size: int):
    starts = [match.start() for match in re.finditer(pattern, content, re.MULTILINE)]
    if len(starts) < 2:
        return size_chunks(content, size)
    bounds = sorted({0, *starts, len(content)})
    chunks = []
    for index, start in enumerate(bounds[:-1]):
        end = bounds[index + 1]
        segment = content[start:end]
        if end - start > size:
            chunks.extend(size_chunks(segment, size, offset=start))
        else:
            chunks.append((start, end, segment))
    return chunks


def chunk_content(content: str, content_type: str, max_size: int, overlap: int):
    selected = detect_content_type(content) if content_type == "auto" else content_type
    patterns = {
        "markdown": r"^#{1,6}\s+",
        "python": r"^(class|def)\s+",
        "log": r"^\d{4}-\d{2}-\d{2}",
    }
    pattern = patterns.get(selected)
    if pattern:
        return regex_chunks(content, pattern, max_size)
    return size_chunks(content, max_size, overlap)


def write_chunks(chunks, output_dir: str, prefix: str):
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    width = max(4, len(str(len(chunks))))
    for index, (_, _, text) in enumerate(chunks):
        path = root / f"{prefix}_{index:0{width}d}.txt"
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description="Create bounded chunks from RLM state")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH)
    parser.add_argument("--type", default="auto", choices=("auto", "markdown", "log", "json", "python", "text"))
    parser.add_argument("--max-size", type=int, default=8_000)
    parser.add_argument("--overlap", type=int, default=0)
    parser.add_argument("--output", "-o", default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--prefix", default="chunk")
    args = parser.parse_args()

    content = load_content(args.state)
    chunks = chunk_content(content, args.type, args.max_size, args.overlap)
    paths = write_chunks(chunks, args.output, args.prefix)

    print(f"Wrote {len(paths)} chunks to {args.output}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
