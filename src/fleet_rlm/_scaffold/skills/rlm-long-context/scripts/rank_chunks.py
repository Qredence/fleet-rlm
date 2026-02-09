#!/usr/bin/env python3
"""
Query-guided chunk selection for RLM workflow.
Ranks chunks by relevance to a query before processing.
"""

from __future__ import annotations

import argparse
import pickle
import re


def load_context(state_path: str) -> str:
    """Load context from RLM state file."""
    with open(state_path, "rb") as f:
        state = pickle.load(f)
    return state.get("content", "")


def rank_chunks_by_query(
    content: str,
    query: str,
    chunk_size: int = 200000,
    top_k: int | None = None,
) -> list[tuple[int, int, float]]:
    """
    Rank chunks by relevance to query.

    Args:
        content: Full text content
        query: User query string
        chunk_size: Size of each chunk in characters
        top_k: Return only top K chunks (None = all)

    Returns:
        List of (start_pos, end_pos, score) tuples, sorted by score descending

    """
    # Extract keywords from query (simple approach)
    keywords = [
        w.lower()
        for w in re.findall(r"\b\w{3,}\b", query)
        if w.lower()
        not in {
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "can",
            "had",
            "her",
            "was",
            "one",
            "our",
            "out",
            "day",
            "get",
            "has",
            "him",
            "his",
            "how",
            "man",
            "new",
            "now",
            "old",
            "see",
            "two",
            "way",
            "who",
            "boy",
            "did",
            "its",
            "let",
            "put",
            "say",
            "she",
            "too",
            "use",
        }
    ]

    # Guard against empty keywords (e.g., query is only stopwords)
    if not keywords:
        # Return all chunks with equal zero score
        scores = []
        for i in range(0, len(content), chunk_size):
            end = min(i + chunk_size, len(content))
            scores.append((i, end, 0.0))
        return scores[:top_k] if top_k else scores

    # Create pattern from keywords
    pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)

    # Score each chunk
    scores = []
    for i in range(0, len(content), chunk_size):
        end = min(i + chunk_size, len(content))
        chunk = content[i:end]

        # Simple scoring: keyword frequency
        matches = len(pattern.findall(chunk))
        score = matches / (len(chunk) / 1000)  # Normalize per 1000 chars

        scores.append((i, end, score))

    # Sort by score descending
    scores.sort(key=lambda x: x[2], reverse=True)

    if top_k:
        scores = scores[:top_k]

    return scores


def main():
    parser = argparse.ArgumentParser(description="Rank chunks by relevance to a query")
    parser.add_argument(
        "--state",
        default=".claude/rlm_state/state.pkl",
        help="Path to RLM state file",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Query to rank chunks against",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200000,
        help="Chunk size in characters",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Return only top K chunks",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file for chunk list (one path per line)",
    )
    parser.add_argument(
        "--chunks-dir",
        default=".claude/rlm_state/chunks",
        help="Directory containing chunk files",
    )

    args = parser.parse_args()

    # Load content
    content = load_context(args.state)

    # Rank chunks
    ranked = rank_chunks_by_query(
        content,
        args.query,
        args.chunk_size,
        args.top_k,
    )

    # Print results
    print(f"Query: {args.query}")
    print(f"Total chunks: {len(list(range(0, len(content), args.chunk_size)))}")
    print(f"Ranked chunks: {len(ranked)}")
    print()
    print(f"{'Rank':<6} {'Chunk':<10} {'Score':<10} {'Range':<20}")
    print("-" * 50)

    chunk_files = []
    for rank, (start, end, score) in enumerate(ranked, 1):
        chunk_idx = start // args.chunk_size
        chunk_file = f"{args.chunks_dir}/chunk_{chunk_idx:04d}.txt"
        chunk_files.append(chunk_file)
        print(f"{rank:<6} {chunk_idx:<10} {score:.2f}       {start:,}-{end:,}")

    # Write output file if requested
    if args.output:
        with open(args.output, "w") as f:
            for cf in chunk_files:
                f.write(f"{cf}\n")
        print(f"\nWrote {len(chunk_files)} chunk paths to {args.output}")


if __name__ == "__main__":
    main()
