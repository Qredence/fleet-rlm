#!/usr/bin/env python3
"""
Main orchestrator for RLM workflow.
Coordinates query-guided selection, semantic chunking, caching, and subagent delegation.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle


class RLMConfig:
    """Configuration for RLM workflow."""

    def __init__(
        self,
        state_path: str = ".claude/rlm_state/state.pkl",
        chunks_dir: str = ".claude/rlm_state/chunks",
        cache_dir: str = ".claude/rlm_state/cache",
        chunk_size: int = 200000,
        overlap: int = 0,
        top_k: int | None = None,
        confidence_threshold: float = 0.95,
        enable_cache: bool = True,
        enable_early_exit: bool = True,
    ):
        self.state_path = state_path
        self.chunks_dir = chunks_dir
        self.cache_dir = cache_dir
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.top_k = top_k
        self.confidence_threshold = confidence_threshold
        self.enable_cache = enable_cache
        self.enable_early_exit = enable_early_exit


def load_content(state_path: str) -> str:
    """Load content from RLM state."""
    with open(state_path, "rb") as f:
        state = pickle.load(f)
    return state.get("content", "")


def run_rank_chunks(
    config: RLMConfig,
    query: str,
) -> list[tuple[int, float]]:
    """Run chunk ranking script."""
    cmd = [
        "python3",
        "-m",
        "skills.rlm_long_context.scripts.rank_chunks",
        "--state",
        config.state_path,
        "--query",
        query,
        "--chunk-size",
        str(config.chunk_size),
    ]

    if config.top_k:
        cmd.extend(["--top-k", str(config.top_k)])

    # Parse output to get ranked chunks
    # This is a simplified version - in practice you'd parse the script output
    content = load_content(config.state_path)
    import re

    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", query)]
    pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)

    scores = []
    for i in range(0, len(content), config.chunk_size):
        chunk = content[i : i + config.chunk_size]
        score = len(pattern.findall(chunk))
        scores.append((i // config.chunk_size, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    if config.top_k:
        scores = scores[: config.top_k]

    return scores


def check_cache(
    cache_dir: str,
    chunk_path: str,
    query: str,
) -> dict | None:
    """Check if result is cached."""
    import hashlib

    key_data = f"{chunk_path}:{query}"
    cache_key = hashlib.sha256(key_data.encode()).hexdigest()[:32]
    cache_path = os.path.join(cache_dir, f"{cache_key}.json")

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    return None


def save_cache(
    cache_dir: str,
    chunk_path: str,
    query: str,
    result: dict,
):
    """Save result to cache."""
    import hashlib

    os.makedirs(cache_dir, exist_ok=True)

    key_data = f"{chunk_path}:{query}"
    cache_key = hashlib.sha256(key_data.encode()).hexdigest()[:32]
    cache_path = os.path.join(cache_dir, f"{cache_key}.json")

    cache_entry = {
        "chunk_path": chunk_path,
        "query": query,
        "cache_key": cache_key,
        "result": result,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_entry, f, indent=2)


def estimate_confidence(results: list[dict], query: str) -> float:
    """Estimate confidence based on results so far."""
    if not results:
        return 0.0

    # Simple heuristic: more high-confidence findings = higher confidence
    total_findings = sum(len(r.get("relevant", [])) for r in results)
    high_conf = sum(
        1
        for r in results
        for f in r.get("relevant", [])
        if f.get("confidence") == "high"
    )

    # Confidence based on finding density and quality
    if total_findings == 0:
        return 0.1

    quality_ratio = high_conf / total_findings if total_findings > 0 else 0
    density = min(total_findings / len(results), 5) / 5  # Cap at 5 findings per chunk

    return min((quality_ratio * 0.7 + density * 0.3), 1.0)


def print_progress(current: int, total: int, confidence: float):
    """Print progress bar."""
    pct = (current / total) * 100
    bar_len = 30
    filled = int(bar_len * current / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(
        f"\r[{bar}] {pct:.1f}% ({current}/{total}) Confidence: {confidence:.2f}", end=""
    )


def orchestrate(
    query: str,
    config: RLMConfig,
) -> list[dict]:
    """Main orchestration loop.

    Args:
        query: User query
        config: RLM configuration

    Returns:
        List of subagent results
    """
    print(f"🔍 Query: {query}")
    print()

    # Step 1: Rank chunks by relevance
    print("📊 Ranking chunks by relevance...")
    ranked_chunks = run_rank_chunks(config, query)
    print(f"   Found {len(ranked_chunks)} chunks to process\n")

    # Step 2: Process chunks with caching and early exit
    results = []
    chunks_to_process = [
        (idx, os.path.join(config.chunks_dir, f"chunk_{idx:04d}.txt"))
        for idx, _ in ranked_chunks
    ]

    print("🤖 Processing chunks...")
    for i, (chunk_idx, chunk_path) in enumerate(chunks_to_process, 1):
        # Check cache first
        if config.enable_cache:
            cached = check_cache(config.cache_dir, chunk_path, query)
            if cached:
                results.append(cached["result"])
                print_progress(
                    i, len(chunks_to_process), estimate_confidence(results, query)
                )
                print(f"  [cached] chunk_{chunk_idx:04d}")
                continue

        # In a real implementation, this would delegate to the subagent
        # For now, we'll simulate with a placeholder
        result = {
            "chunk_id": f"chunk_{chunk_idx:04d}",
            "relevant": [],  # Would be filled by subagent
            "confidence": 0.0,
        }

        # Cache the result
        if config.enable_cache:
            save_cache(config.cache_dir, chunk_path, query, result)

        results.append(result)

        # Update progress
        confidence = estimate_confidence(results, query)
        print_progress(i, len(chunks_to_process), confidence)

        # Early exit check
        if config.enable_early_exit and i >= 3:
            if confidence >= config.confidence_threshold:
                print(
                    f"\n✓ Early exit: confidence {confidence:.2f} >= {config.confidence_threshold}"
                )
                break

    print()  # End progress line
    print(f"\n✅ Processed {len(results)} chunks")

    # Show cache stats
    if config.enable_cache:
        cache_count = (
            len([f for f in os.listdir(config.cache_dir) if f.endswith(".json")])
            if os.path.exists(config.cache_dir)
            else 0
        )
        print(f"💾 Cache entries: {cache_count}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrate RLM workflow with optimizations"
    )
    parser.add_argument(
        "--query",
        "-q",
        required=True,
        help="Query to process",
    )
    parser.add_argument(
        "--state",
        default=".claude/rlm_state/state.pkl",
        help="Path to RLM state file",
    )
    parser.add_argument(
        "--chunks-dir",
        default=".claude/rlm_state/chunks",
        help="Directory containing chunks",
    )
    parser.add_argument(
        "--cache-dir",
        default=".claude/rlm_state/cache",
        help="Cache directory",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200000,
        help="Chunk size",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        help="Process only top K chunks",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.95,
        help="Confidence threshold for early exit",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching",
    )
    parser.add_argument(
        "--no-early-exit",
        action="store_true",
        help="Disable early exit",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file for results (JSON)",
    )

    args = parser.parse_args()

    config = RLMConfig(
        state_path=args.state,
        chunks_dir=args.chunks_dir,
        cache_dir=args.cache_dir,
        chunk_size=args.chunk_size,
        top_k=args.top_k,
        confidence_threshold=args.confidence,
        enable_cache=not args.no_cache,
        enable_early_exit=not args.no_early_exit,
    )

    results = orchestrate(args.query, config)

    # Output results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n📝 Results written to {args.output}")
    else:
        print("\n📋 Results:")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
