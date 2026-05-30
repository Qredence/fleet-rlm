#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re

DEFAULT_CHUNK_SIZE = 200_000
DEFAULT_STATE_PATH = ".codex/rlm_state/state.pkl"
DEFAULT_CHUNKS_DIR = ".codex/rlm_state/chunks"


def load_context(state_path: str) -> str:
    import pickle

    with open(state_path, "rb") as f:
        state = pickle.load(f)  # nosec
    return state.get("content", "")


def rank_chunks_by_query(
    content: str,
    query: str,
    chunk_size: int = 200000,
    top_k: int | None = None,
) -> list[tuple[int, int, float]]:
    stopwords = {
        "and",
        "are",
        "but",
        "for",
        "had",
        "has",
        "her",
        "him",
        "his",
        "not",
        "our",
        "the",
        "you",
        "all",
        "can",
        "was",
        "one",
        "out",
        "get",
        "how",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "did",
        "its",
        "let",
        "put",
        "say",
        "she",
        "too",
        "use",
    }
    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", query) if w.lower() not in stopwords]

    if not keywords:
        scores = []
        for i in range(0, len(content), chunk_size):
            end = min(i + chunk_size, len(content))
            scores.append((i, end, 0.0))
        return scores[:top_k] if top_k is not None else scores

    pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)

    scores = []
    for i in range(0, len(content), chunk_size):
        end = min(i + chunk_size, len(content))
        chunk = content[i:end]
        if not chunk:
            continue

        matches = len(pattern.findall(chunk))
        score = matches / (len(chunk) / 1000)

        scores.append((i, end, score))

    scores.sort(key=lambda x: x[2], reverse=True)

    if top_k is not None:
        scores = scores[:top_k]

    return scores


def main():
    parser = argparse.ArgumentParser(description="Rank chunks by relevance to a query")
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
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
        default=DEFAULT_CHUNK_SIZE,
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
        default=DEFAULT_CHUNKS_DIR,
        help="Directory containing chunk files",
    )

    args = parser.parse_args()

    content = load_context(args.state)

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

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for cf in chunk_files:
                f.write(f"{cf}\n")
        print(f"\nWrote {len(chunk_files)} chunk paths to {args.output}")


if __name__ == "__main__":
    main()
