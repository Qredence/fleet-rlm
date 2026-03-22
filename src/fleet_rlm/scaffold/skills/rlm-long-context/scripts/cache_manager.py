#!/usr/bin/env python3
"""
Cache manager for RLM subagent results.
Avoids re-analyzing chunks for repeated queries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os


def get_cache_key(chunk_path: str, query: str) -> str:
    """Generate cache key from chunk path and query."""
    key_data = f"{chunk_path}:{query}"
    return hashlib.sha256(key_data.encode()).hexdigest()[:32]


def get_cache_path(cache_dir: str, cache_key: str) -> str:
    """Get full cache file path."""
    return os.path.join(cache_dir, f"{cache_key}.json")


def get_cached_result(cache_dir: str, chunk_path: str, query: str) -> dict | None:
    """
    Check if result exists in cache.

    Args:
        cache_dir: Directory containing cache files
        chunk_path: Path to chunk file
        query: Query string

    Returns:
        Cached result dict or None if not found

    """
    cache_key = get_cache_key(chunk_path, query)
    cache_path = get_cache_path(cache_dir, cache_key)

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    return None


def cache_result(
    cache_dir: str,
    chunk_path: str,
    query: str,
    result: dict,
) -> str:
    """
    Cache a subagent result.

    Args:
        cache_dir: Directory for cache files
        chunk_path: Path to chunk file
        query: Query string
        result: Result dict to cache

    Returns:
        Path to cache file

    """
    os.makedirs(cache_dir, exist_ok=True)

    cache_key = get_cache_key(chunk_path, query)
    cache_path = get_cache_path(cache_dir, cache_key)

    # Add metadata
    cache_entry = {
        "chunk_path": chunk_path,
        "query": query,
        "cache_key": cache_key,
        "result": result,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_entry, f, indent=2)

    return cache_path


def invalidate_cache(cache_dir: str, pattern: str | None = None) -> int:
    """
    Invalidate cache entries.

    Args:
        cache_dir: Directory containing cache files
        pattern: If provided, only invalidate keys matching this pattern

    Returns:
        Number of entries invalidated

    """
    if not os.path.exists(cache_dir):
        return 0

    count = 0
    for filename in os.listdir(cache_dir):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(cache_dir, filename)

        if pattern:
            with open(filepath) as f:
                data = json.load(f)
                if pattern not in data.get("query", ""):
                    continue

        os.remove(filepath)
        count += 1

    return count


def list_cache(cache_dir: str) -> list[dict]:
    """
    List all cached entries.

    Args:
        cache_dir: Directory containing cache files

    Returns:
        List of cache entry metadata

    """
    if not os.path.exists(cache_dir):
        return []

    entries = []
    for filename in os.listdir(cache_dir):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(cache_dir, filename)
        with open(filepath) as f:
            data = json.load(f)
            entries.append(
                {
                    "cache_key": data.get("cache_key"),
                    "query": data.get("query", "")[:50] + "...",
                    "chunk": os.path.basename(data.get("chunk_path", "")),
                }
            )

    return entries


def get_cache_stats(cache_dir: str) -> dict:
    """
    Get cache statistics.

    Args:
        cache_dir: Directory containing cache files

    Returns:
        Dict with cache stats

    """
    if not os.path.exists(cache_dir):
        return {"entries": 0, "size_bytes": 0}

    entries = 0
    size_bytes = 0

    for filename in os.listdir(cache_dir):
        if filename.endswith(".json"):
            entries += 1
            filepath = os.path.join(cache_dir, filename)
            size_bytes += os.path.getsize(filepath)

    return {
        "entries": entries,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Manage RLM subagent result cache")
    parser.add_argument(
        "--cache-dir",
        default=".claude/rlm_state/cache",
        help="Cache directory",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # get command
    get_parser = subparsers.add_parser("get", help="Get cached result")
    get_parser.add_argument("--chunk", required=True, help="Chunk file path")
    get_parser.add_argument("--query", required=True, help="Query string")

    # set command
    set_parser = subparsers.add_parser("set", help="Cache a result")
    set_parser.add_argument("--chunk", required=True, help="Chunk file path")
    set_parser.add_argument("--query", required=True, help="Query string")
    set_parser.add_argument("--result", required=True, help="Result JSON string")

    # invalidate command
    inv_parser = subparsers.add_parser("invalidate", help="Invalidate cache")
    inv_parser.add_argument("--pattern", help="Only invalidate matching queries")
    inv_parser.add_argument("--all", action="store_true", help="Clear all cache")

    # list command
    subparsers.add_parser("list", help="List cached entries")

    # stats command
    subparsers.add_parser("stats", help="Show cache statistics")

    args = parser.parse_args()

    if args.command == "get":
        result = get_cached_result(args.cache_dir, args.chunk, args.query)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("null")

    elif args.command == "set":
        result = json.loads(args.result)
        cache_path = cache_result(args.cache_dir, args.chunk, args.query, result)
        print(f"Cached result to {cache_path}")

    elif args.command == "invalidate":
        if args.all:
            count = invalidate_cache(args.cache_dir)
            print(f"Invalidated {count} cache entries")
        elif args.pattern:
            count = invalidate_cache(args.cache_dir, args.pattern)
            print(f"Invalidated {count} entries matching '{args.pattern}'")
        else:
            print("Use --all or --pattern")

    elif args.command == "list":
        entries = list_cache(args.cache_dir)
        print(f"{'Cache Key':<34} {'Chunk':<20} Query")
        print("-" * 80)
        for e in entries:
            print(f"{e['cache_key']:<34} {e['chunk']:<20} {e['query']}")

    elif args.command == "stats":
        stats = get_cache_stats(args.cache_dir)
        print(f"Cache entries: {stats['entries']}")
        print(f"Cache size: {stats['size_mb']} MB")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
