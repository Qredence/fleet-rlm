#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

STOPWORDS = frozenset(
    {
        "all",
        "and",
        "are",
        "but",
        "can",
        "did",
        "for",
        "get",
        "had",
        "has",
        "her",
        "him",
        "his",
        "how",
        "its",
        "let",
        "new",
        "not",
        "now",
        "old",
        "one",
        "our",
        "out",
        "put",
        "say",
        "see",
        "she",
        "the",
        "too",
        "two",
        "use",
        "was",
        "way",
        "who",
        "you",
    }
)


def query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(word.lower() for word in re.findall(r"\b\w{3,}\b", query) if word.lower() not in STOPWORDS)
    )


def score_text(content: str, terms: tuple[str, ...]) -> float:
    if not content or not terms:
        return 0.0
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    return len(pattern.findall(content)) / (len(content) / 1_000)


def rank_chunk_files(chunks_dir: str, query: str, top_k: int | None = None) -> list[tuple[Path, float]]:
    if top_k is not None and top_k < 0:
        raise ValueError("top_k must be non-negative")
    root = Path(chunks_dir)
    paths = tuple(path for path in sorted(root.glob("*.txt")) if path.is_file() and not path.is_symlink())
    terms = query_terms(query)
    ranked = [(path, score_text(path.read_text(encoding="utf-8"), terms)) for path in paths]
    ranked.sort(key=lambda item: (-item[1], str(item[0])))
    return ranked if top_k is None else ranked[:top_k]


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank explicit UTF-8 chunk files by lexical query matches")
    parser.add_argument("--chunks-dir", required=True, help="Directory containing direct *.txt children")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int)
    args = parser.parse_args()

    for path, score in rank_chunk_files(args.chunks_dir, args.query, args.top_k):
        print(f"{path}\t{score:.6f}")


if __name__ == "__main__":
    main()
