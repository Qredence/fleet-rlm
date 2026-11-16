"""Probe large files, search line/chunk windows with provenance, and prepare Jev batches."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_MAX_LINE_BYTES = 1_000_000
_MINIFIED_LINE_BYTES = 16_384
_MINIFIED_MAX_LINES = 5
_READ_BLOCK_BYTES = 65_536
_JEV_MAX_BATCH = 100


def _require_pos_int(value: int, label: str, *, allow_zero: bool = False) -> int:
    """Validate an integer bound, rejecting booleans and negative values."""
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        bound_desc = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be a {bound_desc} integer")
    return value


def _probe_one(path: str | Path) -> dict[str, Any]:
    """Stream-probe one file for size, binary bytes, line count, and max line length."""
    target = Path(path)
    if not target.exists() or not target.is_file():
        return {
            "path": str(target),
            "exists": False,
            "size_bytes": 0,
            "is_binary": False,
            "line_count": 0,
            "max_line_bytes": 0,
            "has_long_lines": False,
            "is_minified": False,
            "recommended_mode": "missing",
        }

    size_bytes = target.stat().st_size
    is_binary = False
    newline_count = 0
    current_line = 0
    max_line = 0
    last_byte = b""

    with target.open("rb") as handle:
        first_chunk = True
        while chunk := handle.read(_READ_BLOCK_BYTES):
            if first_chunk:
                is_binary = b"\x00" in chunk[:8192]
                first_chunk = False
                if is_binary:
                    break
            last_byte = chunk[-1:]
            start = 0
            while (nl_pos := chunk.find(b"\n", start)) != -1:
                newline_count += 1
                line_len = current_line + (nl_pos - start)
                if line_len > max_line:
                    max_line = line_len
                current_line = 0
                start = nl_pos + 1
            current_line += len(chunk) - start

    if current_line > max_line:
        max_line = current_line
    if is_binary:
        line_count: int | None = None
    elif size_bytes == 0:
        line_count = 0
    else:
        line_count = newline_count + (0 if last_byte == b"\n" else 1)

    has_long_lines = max_line > _MAX_LINE_BYTES
    is_minified = (
        not is_binary
        and size_bytes > 0
        and (has_long_lines or ((line_count or 0) <= _MINIFIED_MAX_LINES and max_line > _MINIFIED_LINE_BYTES))
    )
    recommended_mode = "binary" if is_binary else ("chunks" if is_minified else "lines")
    return {
        "path": str(target),
        "exists": True,
        "size_bytes": size_bytes,
        "is_binary": is_binary,
        "line_count": line_count,
        "max_line_bytes": max_line,
        "has_long_lines": has_long_lines,
        "is_minified": is_minified,
        "recommended_mode": recommended_mode,
    }


def probe_files(paths: str | Path | Sequence[str | Path]) -> list[dict[str, Any]]:
    """Inspect file sizes, binary presence, line counts, and recommended read modes."""
    path_list = [paths] if isinstance(paths, (str, Path)) else list(paths)
    return [_probe_one(p) for p in path_list]


def _resolve_input(path_or_text: str | Path, *, chunk_size: int) -> tuple[Path | None, str | None, str]:
    """Classify path_or_text as a file path or raw text and select lines vs chunks mode."""
    if isinstance(path_or_text, Path):
        if not path_or_text.is_file():
            raise FileNotFoundError(f"Search target file not found: {path_or_text}")
        probe = _probe_one(path_or_text)
        if probe["is_binary"]:
            raise ValueError(f"Cannot run text search on binary file: {path_or_text}")
        use_chunks = probe["has_long_lines"] or (
            (probe["line_count"] or 0) <= _MINIFIED_MAX_LINES and probe["size_bytes"] > max(chunk_size * 2, 16_384)
        )
        return path_or_text, None, ("chunks" if use_chunks else "lines")

    if not isinstance(path_or_text, str):
        raise TypeError("path_or_text must be a file path or string")

    if "\n" not in path_or_text and "\x00" not in path_or_text and len(path_or_text) <= 4096:
        candidate = Path(path_or_text)
        if candidate.is_file():
            return _resolve_input(candidate, chunk_size=chunk_size)
        if path_or_text.startswith(("/workspace/", "files/", "artifacts/")):
            raise FileNotFoundError(f"Search target file not found: {path_or_text}")

    lines = path_or_text.splitlines()
    max_len = max((len(line) for line in lines), default=0)
    use_chunks = max_len > _MAX_LINE_BYTES or (
        len(lines) <= _MINIFIED_MAX_LINES and len(path_or_text) > max(chunk_size * 2, 16_384)
    )
    return None, path_or_text, ("chunks" if use_chunks else "lines")


def _search_lines_iter(
    line_iter: Any,
    regex: re.Pattern[str],
    *,
    context_lines: int,
    max_hits: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Stream lines and collect bounded context windows around matches."""
    before: deque[tuple[int, str]] = deque(maxlen=context_lines)
    active: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []
    truncated = False

    for line_no, raw_line in enumerate(line_iter, 1):
        clean_line = raw_line.rstrip("\r\n")
        for item in active:
            if line_no <= item["target_end"]:
                item["window"].append((line_no, clean_line))

        still_active: list[dict[str, Any]] = []
        for item in active:
            if line_no >= item["target_end"]:
                finished.append(item)
            else:
                still_active.append(item)
        active = still_active

        if len(finished) + len(active) >= max_hits:
            if not active and regex.search(clean_line):
                truncated = True
                break
            if active:
                if regex.search(clean_line):
                    truncated = True
                continue

        match = regex.search(clean_line)
        if match is not None:
            window = [*list(before), (line_no, clean_line)]
            entry = {
                "match_index": len(finished) + len(active),
                "line_number": line_no,
                "start_line": window[0][0],
                "target_end": line_no + context_lines,
                "match": match.group(0),
                "window": window,
            }
            if context_lines == 0:
                finished.append(entry)
            else:
                active.append(entry)
        before.append((line_no, clean_line))

    finished.extend(active)
    hits = [
        {
            "match_index": item["match_index"],
            "line_number": item["line_number"],
            "start_line": item["start_line"],
            "end_line": item["window"][-1][0],
            "match": item["match"],
            "excerpt": "\n".join(f"{ln}: {txt}" for ln, txt in item["window"]),
        }
        for item in finished[:max_hits]
    ]
    return hits, truncated


def _search_chunks_stream(
    file_path: Path | None,
    raw_text: str | None,
    regex: re.Pattern[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
    max_hits: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Search overlapping character windows so >1 MB lines and minified files stay bounded."""
    step = chunk_size - chunk_overlap
    hits: list[dict[str, Any]] = []
    seen_starts: set[int] = set()
    truncated = False

    def scan_window(window: str, base_offset: int, chunk_idx: int) -> bool:
        nonlocal truncated
        for match in regex.finditer(window):
            abs_start = base_offset + match.start()
            if abs_start in seen_starts:
                continue
            if len(hits) >= max_hits:
                truncated = True
                return True
            seen_starts.add(abs_start)
            hits.append(
                {
                    "match_index": len(hits),
                    "chunk_index": chunk_idx,
                    "start_offset": abs_start,
                    "end_offset": base_offset + match.end(),
                    "match": match.group(0),
                    "excerpt": window,
                }
            )
        return False

    if raw_text is not None:
        total = len(raw_text)
        offset = 0
        chunk_idx = 0
        while offset < total:
            window = raw_text[offset : offset + chunk_size]
            if scan_window(window, offset, chunk_idx):
                break
            if offset + chunk_size >= total:
                break
            offset += step
            chunk_idx += 1
        return hits, truncated

    assert file_path is not None
    block_chars = max(65_536, chunk_size)
    buf = ""
    base_offset = 0
    chunk_idx = 0
    eof = False
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        while not eof or buf:
            while not eof and len(buf) < chunk_size:
                piece = handle.read(block_chars)
                if not piece:
                    eof = True
                    break
                buf += piece
            if not buf:
                break
            window = buf[:chunk_size]
            if scan_window(window, base_offset, chunk_idx):
                break
            if eof and len(buf) <= chunk_size:
                break
            advance = min(step, len(buf))
            buf = buf[advance:]
            base_offset += advance
            chunk_idx += 1
    return hits, truncated


def search_windows(
    path_or_text: str | Path,
    pattern: str,
    *,
    context_lines: int = 4,
    chunk_size: int = 4000,
    chunk_overlap: int = 300,
    max_hits: int = 50,
    ignore_case: bool = True,
) -> dict[str, Any]:
    """Search a file or text string, switching automatically to overlapping chunks on huge/minified lines."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    _require_pos_int(context_lines, "context_lines", allow_zero=True)
    _require_pos_int(chunk_size, "chunk_size")
    _require_pos_int(chunk_overlap, "chunk_overlap", allow_zero=True)
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    _require_pos_int(max_hits, "max_hits")

    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(pattern, flags)
    file_path, raw_text, mode = _resolve_input(path_or_text, chunk_size=chunk_size)

    if mode == "chunks":
        hits, truncated = _search_chunks_stream(
            file_path,
            raw_text,
            regex,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_hits=max_hits,
        )
    elif file_path is not None:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            hits, truncated = _search_lines_iter(handle, regex, context_lines=context_lines, max_hits=max_hits)
    else:
        assert raw_text is not None
        hits, truncated = _search_lines_iter(
            raw_text.splitlines(),
            regex,
            context_lines=context_lines,
            max_hits=max_hits,
        )

    return {
        "mode": mode,
        "pattern": pattern,
        "hit_count": len(hits),
        "truncated": truncated,
        "hits": hits,
    }


def read_line_range(
    path: str | Path,
    start_line: int,
    end_line: int,
    *,
    max_line_chars: int = _MAX_LINE_BYTES,
) -> dict[str, Any]:
    """Read an inclusive 1-based line range from a file, guarding against lines larger than 1 MB."""
    _require_pos_int(start_line, "start_line")
    _require_pos_int(end_line, "end_line")
    _require_pos_int(max_line_chars, "max_line_chars")
    if end_line < start_line:
        raise ValueError("end_line must be greater than or equal to start_line")

    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")

    collected: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, 1):
            if len(raw_line) > max_line_chars:
                raise ValueError(f"Line {line_no} exceeds {max_line_chars} chars; use search_windows chunk mode")
            if line_no < start_line:
                continue
            if line_no > end_line:
                break
            collected.append({"line_number": line_no, "text": raw_line.rstrip("\r\n")})

    actual_end = collected[-1]["line_number"] if collected else start_line - 1
    formatted = "\n".join(f"{item['line_number']}: {item['text']}" for item in collected)
    return {
        "path": str(target),
        "start_line": start_line,
        "end_line": actual_end,
        "lines": collected,
        "text": formatted,
    }


def build_jev_batches(
    windows: Mapping[str, Any] | Sequence[Mapping[str, Any] | str],
    question: str,
    *,
    max_chars_per_item: int = 3000,
) -> list[dict[str, Any]]:
    """Format search windows into bounded provenance-tagged batches of at most 100 items for jev_decide_batched."""
    if not isinstance(question, str) or not question.strip() or len(question) > 10_000:
        raise ValueError("question must be a non-empty string up to 10,000 characters")
    _require_pos_int(max_chars_per_item, "max_chars_per_item")

    raw_windows = windows["hits"] if isinstance(windows, Mapping) and "hits" in windows else list(windows)
    formatted_items: list[tuple[str, dict[str, Any]]] = []

    for idx, entry in enumerate(raw_windows):
        if isinstance(entry, str):
            item_text = entry[:max_chars_per_item]
            meta: dict[str, Any] = {"index": idx}
        elif isinstance(entry, Mapping):
            excerpt = str(entry.get("excerpt", ""))
            if "line_number" in entry:
                start_ln = entry.get("start_line", entry["line_number"])
                end_ln = entry.get("end_line", entry["line_number"])
                header = f"[lines {start_ln}-{end_ln} | hit line {entry['line_number']}]\n"
                meta = {
                    "index": idx,
                    "line_number": entry["line_number"],
                    "start_line": start_ln,
                    "end_line": end_ln,
                }
            elif "chunk_index" in entry:
                start_off = entry.get("start_offset", 0)
                end_off = entry.get("end_offset", 0)
                header = f"[chunk {entry['chunk_index']} | offsets {start_off}-{end_off}]\n"
                meta = {
                    "index": idx,
                    "chunk_index": entry["chunk_index"],
                    "start_offset": start_off,
                    "end_offset": end_off,
                }
            else:
                header = ""
                meta = {"index": idx}
            item_text = (header + excerpt)[:max_chars_per_item]
        else:
            raise TypeError(f"Window entry at index {idx} must be a mapping or string")

        if item_text:
            formatted_items.append((item_text, meta))

    batches: list[dict[str, Any]] = []
    for batch_idx, offset in enumerate(range(0, len(formatted_items), _JEV_MAX_BATCH)):
        chunk = formatted_items[offset : offset + _JEV_MAX_BATCH]
        batches.append(
            {
                "batch_index": batch_idx,
                "items": [text for text, _ in chunk],
                "question": question,
                "metadata": [meta for _, meta in chunk],
            }
        )
    return batches


def run(
    path_or_text: str | Path,
    pattern: str,
    *,
    context_lines: int = 4,
    max_hits: int = 50,
) -> dict[str, Any]:
    """Probe if path and search bounded line or chunk windows for pattern."""
    return search_windows(path_or_text, pattern, context_lines=context_lines, max_hits=max_hits)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for probing files, searching bounded windows, or reading line ranges."""
    parser = argparse.ArgumentParser(description="Search and probe large files with bounded context windows.")
    parser.add_argument("path", help="Target file path")
    parser.add_argument("pattern", nargs="?", default=None, help="Optional regex or text pattern to search")
    parser.add_argument("--probe", action="store_true", help="Only probe file size, line count, and mode")
    parser.add_argument("--start-line", type=int, default=None, help="Start line (1-based) for read_line_range")
    parser.add_argument("--end-line", type=int, default=None, help="End line (1-based) for read_line_range")
    parser.add_argument("--context-lines", type=int, default=4, help="Lines of context around matches (default: 4)")
    parser.add_argument("--max-hits", type=int, default=50, help="Maximum hits to return (default: 50)")
    args = parser.parse_args(argv)

    try:
        if args.probe:
            result: Any = probe_files([args.path])
        elif args.start_line is not None or args.end_line is not None:
            if args.start_line is None or args.end_line is None:
                raise ValueError("Both --start-line and --end-line are required together")
            result = read_line_range(args.path, args.start_line, args.end_line)
        elif args.pattern is not None:
            result = search_windows(
                args.path,
                args.pattern,
                context_lines=args.context_lines,
                max_hits=args.max_hits,
            )
        else:
            result = probe_files([args.path])
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
