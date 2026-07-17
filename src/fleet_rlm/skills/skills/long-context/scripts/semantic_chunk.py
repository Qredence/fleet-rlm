#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from itertools import pairwise
from pathlib import Path

Chunk = tuple[int, int, str]


def read_content(input_path: str) -> str:
    return Path(input_path).read_text(encoding="utf-8")


def detect_content_type(content: str) -> str:
    text = content.lstrip()
    probes = (
        ("json", text[:1] in "{["),
        ("markdown", text.startswith("#") or "\n#" in content),
        ("python", re.search(r"^(?:async\s+def|class|def)\s+", content, re.MULTILINE) is not None),
        ("log", re.search(r"^\d{4}-\d{2}-\d{2}", content, re.MULTILINE) is not None),
    )
    return next((name for name, matched in probes if matched), "text")


def size_chunks(content: str, size: int, overlap: int = 0, offset: int = 0) -> list[Chunk]:
    if size < 1:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be non-negative and smaller than size")
    if not content:
        return [(offset, offset, "")]

    chunks: list[Chunk] = []
    stride = size - overlap
    for start in range(0, len(content), stride):
        end = min(start + size, len(content))
        chunks.append((offset + start, offset + end, content[start:end]))
        if end == len(content):
            break
    return chunks


def regex_chunks(content: str, pattern: str, size: int, overlap: int) -> list[Chunk]:
    starts = [match.start() for match in re.finditer(pattern, content, re.MULTILINE)]
    if not starts:
        return size_chunks(content, size)
    bounds = sorted({0, *starts, len(content)})
    chunks: list[Chunk] = []
    for start, end in pairwise(bounds):
        if start == end:
            continue
        segment = content[start:end]
        chunks.extend(
            size_chunks(segment, size, overlap=overlap, offset=start)
            if len(segment) > size
            else [(start, end, segment)]
        )
    return chunks


def chunk_content(content: str, content_type: str, max_size: int, overlap: int) -> list[Chunk]:
    selected = detect_content_type(content) if content_type == "auto" else content_type
    patterns = {
        "markdown": r"^#{1,6}\s+",
        "python": r"^(?:async\s+def|class|def)\s+",
        "log": r"^\d{4}-\d{2}-\d{2}",
    }
    pattern = patterns.get(selected)
    if pattern:
        return regex_chunks(content, pattern, max_size, overlap)
    return size_chunks(content, max_size, overlap)


def write_chunks(chunks: list[Chunk], output_dir: str, prefix: str) -> list[tuple[Path, int, int]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", prefix):
        raise ValueError("prefix must be a plain filename stem")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("output directory must be empty")
    width = max(4, len(str(max(0, len(chunks) - 1))))
    written: list[tuple[Path, int, int]] = []
    for index, (start, end, text) in enumerate(chunks):
        path = root / f"{prefix}_{index:0{width}d}.txt"
        path.write_text(text, encoding="utf-8")
        written.append((path, start, end))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Split one explicit UTF-8 file into deterministic chunks")
    parser.add_argument("--input", required=True, help="UTF-8 source file")
    parser.add_argument(
        "--type",
        default="auto",
        choices=("auto", "markdown", "log", "json", "python", "text"),
    )
    parser.add_argument("--max-size", type=int, default=8_000)
    parser.add_argument("--overlap", type=int, default=0)
    parser.add_argument("--output", required=True, help="Output directory for chunk files")
    parser.add_argument("--prefix", default="chunk")
    args = parser.parse_args()

    chunks = chunk_content(read_content(args.input), args.type, args.max_size, args.overlap)
    for path, start, end in write_chunks(chunks, args.output, args.prefix):
        print(f"{path.name}\t{start}\t{end}")


if __name__ == "__main__":
    main()
