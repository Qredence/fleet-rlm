#!/usr/bin/env python3
"""S-NIAH (Single Needle in a Haystack) benchmark generator.

Generates synthetic tasks where a specific fact is hidden in a large body of
filler text. The RLM must write code to search the text and extract the answer.

Aligned with the S-NIAH benchmark from arXiv 2512.24601v2 (RLM paper).

Usage:
    uv run python scripts/benchmarks/sniah.py --generate
    uv run python scripts/benchmarks/sniah.py --generate --count 20 --output custom.json
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / ".data/datasets/sniah-benchmark.json"
)
DEFAULT_COUNT = 50

FILLER_VOCABULARY = [
    "the",
    "be",
    "to",
    "of",
    "and",
    "a",
    "in",
    "that",
    "have",
    "I",
    "it",
    "for",
    "not",
    "on",
    "with",
    "he",
    "as",
    "you",
    "do",
    "at",
    "this",
    "but",
    "his",
    "by",
    "from",
    "they",
    "we",
    "say",
    "her",
    "she",
    "or",
    "an",
    "will",
    "my",
    "one",
    "all",
    "would",
    "there",
    "their",
    "what",
    "so",
    "up",
    "out",
    "if",
    "about",
    "who",
    "get",
    "which",
    "go",
    "me",
    "when",
    "make",
    "can",
    "like",
    "time",
    "no",
    "just",
    "him",
    "know",
    "take",
    "people",
    "into",
    "year",
    "your",
    "good",
    "some",
    "could",
    "them",
    "see",
    "other",
    "than",
    "then",
    "now",
    "look",
    "only",
    "come",
    "its",
    "over",
    "think",
    "also",
    "back",
    "after",
    "use",
    "two",
    "how",
    "our",
    "work",
    "first",
    "well",
    "way",
    "even",
    "new",
    "want",
    "because",
    "any",
    "these",
    "give",
    "day",
    "most",
    "us",
    "great",
    "between",
    "need",
    "large",
    "often",
    "important",
    "general",
    "development",
    "system",
    "program",
    "question",
    "government",
    "company",
    "number",
    "part",
    "during",
    "problem",
    "fact",
    "group",
    "however",
    "place",
    "point",
    "research",
    "change",
    "world",
    "area",
    "country",
    "community",
    "family",
    "history",
    "information",
    "power",
    "student",
    "process",
    "report",
    "service",
    "study",
    "market",
    "provide",
    "health",
    "society",
    "university",
    "policy",
    "national",
]

NEEDLE_TEMPLATES = {
    "code": {
        "values": lambda: (
            "".join(random.choices(string.ascii_uppercase, k=5))
            + "-"
            + "".join(random.choices(string.digits, k=4))
        ),
        "sentence": "The secret access code is {val}.",
        "question": "What is the secret access code mentioned in the text?",
    },
    "date": {
        "values": lambda: (
            f"{random.randint(2025, 2030)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        ),
        "sentence": "The critical deadline is {val}.",
        "question": "What is the critical deadline mentioned in the text?",
    },
    "name": {
        "values": lambda: random.choice(
            [
                "Dr. Evelyn Hartwell",
                "Prof. Marcus Steinberg",
                "Cmdr. Yuki Tanaka",
                "Agent Rachel Okonkwo",
                "Sir Alistair Pemberton",
            ]
        ),
        "sentence": "The project lead assigned to Operation Nexus is {val}.",
        "question": "Who is the project lead assigned to Operation Nexus?",
    },
    "number": {
        "values": lambda: str(random.randint(10000, 99999)),
        "sentence": "The final inventory count for warehouse Q7 is exactly {val} units.",
        "question": "What is the final inventory count for warehouse Q7?",
    },
}


def generate_filler_paragraph(min_words: int = 40, max_words: int = 120) -> str:
    length = random.randint(min_words, max_words)
    words = [random.choice(FILLER_VOCABULARY) for _ in range(length)]
    words[0] = words[0].capitalize()
    sentences = []
    i = 0
    while i < len(words):
        sent_len = random.randint(6, 18)
        sent_words = words[i : i + sent_len]
        if sent_words:
            sent_words[0] = sent_words[0].capitalize()
            sentences.append(" ".join(sent_words) + ".")
        i += sent_len
    return " ".join(sentences)


def generate_haystack(target_chars: int) -> str:
    paragraphs = []
    total = 0
    while total < target_chars:
        para = generate_filler_paragraph()
        paragraphs.append(para)
        total += len(para) + 2
    return "\n\n".join(paragraphs)[:target_chars]


def generate_needle(needle_type: str) -> tuple[str, str, str]:
    template = NEEDLE_TEMPLATES[needle_type]
    val = template["values"]()
    sentence = template["sentence"].format(val=val)
    question = template["question"]
    return sentence, question, val


def generate_sniah_dataset(count: int = DEFAULT_COUNT, seed: int = 42) -> list[dict]:
    random.seed(seed)
    sizes = [50_000, 100_000, 200_000]
    depths = [0.25, 0.50, 0.75, 0.90]
    types = list(NEEDLE_TEMPLATES.keys())
    tasks = []

    for i in range(count):
        haystack_size = sizes[i % len(sizes)]
        depth = depths[i % len(depths)]
        needle_type = types[i % len(types)]

        haystack = generate_haystack(haystack_size)
        needle_sentence, question, expected = generate_needle(needle_type)

        insert_pos = int(len(haystack) * depth)
        full_text = (
            haystack[:insert_pos]
            + "\n\n"
            + needle_sentence
            + "\n\n"
            + haystack[insert_pos:]
        )

        tasks.append(
            {
                "id": f"sniah-{i + 1:03d}",
                "benchmark": "sniah",
                "haystack_target_chars": haystack_size,
                "haystack_chars": len(full_text),
                "needle_depth": depth,
                "needle_type": needle_type,
                "context": full_text,
                "question": question,
                "expected_answer": expected,
            }
        )

    return tasks


def score_sniah(answer: str, expected: str) -> float:
    if not answer or not expected:
        return 0.0
    if expected in answer:
        return 1.0
    if expected.lower() in answer.lower():
        return 0.5
    return 0.0


def aggregate_sniah_results(results: list[dict]) -> dict:
    if not results:
        return {"benchmark": "sniah", "tasks_total": 0, "accuracy": 0.0}

    scores = [r["score"] for r in results]
    by_depth: dict[str, list[float]] = {}
    by_size: dict[str, list[float]] = {}
    by_type: dict[str, list[float]] = {}

    for r in results:
        d = str(r.get("needle_depth", "?"))
        s = str(r.get("haystack_target_chars", r.get("haystack_chars", "?")))
        t = str(r.get("needle_type", "?"))
        by_depth.setdefault(d, []).append(r["score"])
        by_size.setdefault(s, []).append(r["score"])
        by_type.setdefault(t, []).append(r["score"])

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "benchmark": "sniah",
        "tasks_total": len(results),
        "accuracy": _avg(scores),
        "by_depth": {k: round(_avg(v), 3) for k, v in sorted(by_depth.items())},
        "by_size": {k: round(_avg(v), 3) for k, v in sorted(by_size.items())},
        "by_type": {k: round(_avg(v), 3) for k, v in sorted(by_type.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="S-NIAH benchmark generator")
    parser.add_argument("--generate", action="store_true", help="Generate dataset")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    if not args.generate:
        parser.print_help()
        sys.exit(0)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    tasks = generate_sniah_dataset(count=args.count, seed=args.seed)
    output.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Generated {len(tasks)} S-NIAH tasks → {output}")
    sizes = set(t["haystack_target_chars"] for t in tasks)
    depths = set(t["needle_depth"] for t in tasks)
    types = set(t["needle_type"] for t in tasks)
    print(f"  Sizes: {sorted(sizes)}")
    print(f"  Depths: {sorted(depths)}")
    print(f"  Types: {sorted(types)}")


if __name__ == "__main__":
    main()
