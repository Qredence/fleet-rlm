#!/usr/bin/env python3
"""OOLONG-style aggregation benchmark generator.

Generates synthetic tasks that require programmatic iteration over structured
data to count, classify, or extract matching rows. The RLM must write Python
code to process the data — it cannot fit in a single LLM context window.

Aligned with the OOLONG benchmark from arXiv 2512.24601v2 (RLM paper).
Scoring uses the paper's metric: score(ŷ) = 0.75^|y - ŷ| for numeric answers.

Usage:
    uv run python scripts/benchmarks/oolong.py --generate
    uv run python scripts/benchmarks/oolong.py --generate --count 30 --output custom.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / ".data/datasets/oolong-benchmark.json"
)
DEFAULT_COUNT = 30

PRODUCT_CATEGORIES = [
    "electronics",
    "clothing",
    "food",
    "books",
    "tools",
    "furniture",
    "toys",
    "sports",
]
SENTIMENT_WORDS = {
    "positive": [
        "excellent",
        "great",
        "wonderful",
        "fantastic",
        "love",
        "amazing",
        "perfect",
        "outstanding",
        "superb",
        "delighted",
        "thrilled",
        "impressed",
    ],
    "negative": [
        "terrible",
        "awful",
        "horrible",
        "worst",
        "hate",
        "disappointing",
        "broken",
        "defective",
        "useless",
        "frustrated",
        "angry",
        "regret",
    ],
    "neutral": [
        "okay",
        "average",
        "standard",
        "typical",
        "normal",
        "expected",
        "adequate",
        "reasonable",
        "acceptable",
        "fine",
        "moderate",
        "decent",
    ],
}
LOG_LEVELS = ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"]
SERVICE_NAMES = [
    "auth-service",
    "api-gateway",
    "worker-pool",
    "cache-layer",
    "db-proxy",
    "scheduler",
]


# ---------------------------------------------------------------------------
# Category A — Counting tasks
# ---------------------------------------------------------------------------


def _random_product_name() -> str:
    adjectives = ["Premium", "Basic", "Pro", "Lite", "Ultra", "Mini", "Max", "Eco"]
    nouns = ["Widget", "Gadget", "Module", "Unit", "Pack", "Set", "Kit", "Box"]
    return (
        f"{random.choice(adjectives)} {random.choice(nouns)} {random.randint(100, 999)}"
    )


def generate_counting_task(index: int) -> dict[str, Any]:
    target_category = random.choice(PRODUCT_CATEGORIES)
    item_count = random.randint(200, 500)
    items = []
    expected_count = 0

    for _ in range(item_count):
        cat = random.choice(PRODUCT_CATEGORIES)
        price = round(random.uniform(5.0, 500.0), 2)
        items.append({"name": _random_product_name(), "category": cat, "price": price})
        if cat == target_category:
            expected_count += 1

    return {
        "id": f"oolong-count-{index + 1:03d}",
        "benchmark": "oolong",
        "task_type": "counting",
        "item_count": item_count,
        "context": json.dumps(items),
        "question": (
            f"The variable `data` contains a JSON list of {item_count} product items, "
            f"each with 'name', 'category', and 'price' fields. "
            f"How many items are in the '{target_category}' category? "
            f"Answer with just the number."
        ),
        "expected_answer": str(expected_count),
        "expected_numeric": expected_count,
    }


# ---------------------------------------------------------------------------
# Category B — Classification tasks
# ---------------------------------------------------------------------------


def _generate_review(sentiment: str) -> str:
    words = random.sample(
        SENTIMENT_WORDS[sentiment], min(3, len(SENTIMENT_WORDS[sentiment]))
    )
    filler = random.choice(
        [
            "The product was",
            "My experience was",
            "Overall it was",
            "I found it",
            "This item is",
            "The quality is",
        ]
    )
    return f"{filler} {words[0]} and {words[1]}. Really {words[2]}."


def generate_classification_task(index: int) -> dict[str, Any]:
    review_count = random.randint(100, 300)
    reviews = []
    counts = {"positive": 0, "negative": 0, "neutral": 0}

    for _ in range(review_count):
        sentiment = random.choices(
            ["positive", "negative", "neutral"], weights=[0.4, 0.3, 0.3]
        )[0]
        reviews.append({"text": _generate_review(sentiment), "label": sentiment})
        counts[sentiment] += 1

    context_items = [{"id": i + 1, "text": r["text"]} for i, r in enumerate(reviews)]

    return {
        "id": f"oolong-classify-{index + 1:03d}",
        "benchmark": "oolong",
        "task_type": "classification",
        "item_count": review_count,
        "context": json.dumps(context_items),
        "question": (
            f"The variable `data` contains a JSON list of {review_count} product reviews, "
            f"each with 'id' and 'text' fields. Classify each review as positive, negative, "
            f"or neutral based on the sentiment words used. A review is positive if it contains "
            f"words like 'excellent', 'great', 'wonderful', 'fantastic', 'love', 'amazing'; "
            f"negative if it contains 'terrible', 'awful', 'horrible', 'worst', 'hate', "
            f"'disappointing'; neutral otherwise. "
            f"Return the count of each category as: positive=N negative=M neutral=K"
        ),
        "expected_answer": f"positive={counts['positive']} negative={counts['negative']} neutral={counts['neutral']}",
        "expected_counts": counts,
    }


# ---------------------------------------------------------------------------
# Category C — Extraction tasks
# ---------------------------------------------------------------------------


def _random_ip() -> str:
    return f"{random.randint(10, 192)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


def _random_timestamp() -> str:
    h = random.randint(0, 23)
    m = random.randint(0, 59)
    s = random.randint(0, 59)
    return f"2026-04-{random.randint(10, 28):02d}T{h:02d}:{m:02d}:{s:02d}Z"


def generate_extraction_task(index: int) -> dict[str, Any]:
    row_count = random.randint(100, 500)
    target_level = random.choice(["ERROR", "CRITICAL"])
    target_service = random.choice(SERVICE_NAMES)
    rows = []
    expected_count = 0

    for _ in range(row_count):
        level = random.choices(LOG_LEVELS, weights=[40, 20, 15, 20, 5])[0]
        service = random.choice(SERVICE_NAMES)
        msg = f"Request from {_random_ip()} processed in {random.randint(1, 5000)}ms"
        rows.append(f"{_random_timestamp()} [{level}] {service}: {msg}")
        if level == target_level and service == target_service:
            expected_count += 1

    return {
        "id": f"oolong-extract-{index + 1:03d}",
        "benchmark": "oolong",
        "task_type": "extraction",
        "item_count": row_count,
        "context": "\n".join(rows),
        "question": (
            f"The variable `data` contains {row_count} log lines. Each line has format: "
            f"'timestamp [LEVEL] service: message'. "
            f"How many log lines have level '{target_level}' AND service '{target_service}'? "
            f"Answer with just the number."
        ),
        "expected_answer": str(expected_count),
        "expected_numeric": expected_count,
        "filter_level": target_level,
        "filter_service": target_service,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_oolong_numeric(answer: str, expected: int) -> float:
    """Paper's OOLONG scoring: 0.75^|y - ŷ|."""
    if not answer:
        return 0.0
    numbers = re.findall(r"\d+", answer)
    if not numbers:
        return 0.0
    try:
        predicted = int(numbers[0])
    except ValueError:
        return 0.0
    return 0.75 ** abs(expected - predicted)


def score_oolong_classification(answer: str, expected_counts: dict[str, int]) -> float:
    """Average 0.75^|y_i - ŷ_i| across categories."""
    scores = []
    for category, expected_val in expected_counts.items():
        pattern = rf"{category}\s*[=:]\s*(\d+)"
        match = re.search(pattern, answer.lower())
        if match:
            predicted = int(match.group(1))
            scores.append(0.75 ** abs(expected_val - predicted))
        else:
            scores.append(0.0)
    return sum(scores) / len(scores) if scores else 0.0


def score_oolong_task(task: dict[str, Any], answer: str) -> float:
    task_type = task.get("task_type", "counting")
    if task_type == "classification":
        return score_oolong_classification(answer, task["expected_counts"])
    return score_oolong_numeric(answer, task["expected_numeric"])


def aggregate_oolong_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"benchmark": "oolong", "tasks_total": 0, "avg_score": 0.0}

    scores = [r["score"] for r in results]
    by_type: dict[str, list[float]] = {}
    for r in results:
        t = r.get("task_type", "?")
        by_type.setdefault(t, []).append(r["score"])

    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "benchmark": "oolong",
        "tasks_total": len(results),
        "avg_score": round(_avg(scores), 4),
        "by_type": {k: round(_avg(v), 4) for k, v in sorted(by_type.items())},
        "perfect_scores": sum(1 for s in scores if s >= 0.99),
        "near_miss": sum(1 for s in scores if 0.5 <= s < 0.99),
        "failures": sum(1 for s in scores if s < 0.5),
    }


# ---------------------------------------------------------------------------
# Dataset generation
# ---------------------------------------------------------------------------


def generate_oolong_dataset(
    count: int = DEFAULT_COUNT, seed: int = 42
) -> list[dict[str, Any]]:
    random.seed(seed)
    counting_n = count // 3
    classify_n = count // 3
    extract_n = count - counting_n - classify_n

    tasks: list[dict[str, Any]] = []
    for i in range(counting_n):
        tasks.append(generate_counting_task(i))
    for i in range(classify_n):
        tasks.append(generate_classification_task(i))
    for i in range(extract_n):
        tasks.append(generate_extraction_task(i))

    random.shuffle(tasks)
    for i, task in enumerate(tasks):
        task["id"] = f"oolong-{i + 1:03d}"

    return tasks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OOLONG-style aggregation benchmark generator"
    )
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

    tasks = generate_oolong_dataset(count=args.count, seed=args.seed)
    output.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    types = {}
    for t in tasks:
        tt = t["task_type"]
        types[tt] = types.get(tt, 0) + 1

    print(f"Generated {len(tasks)} OOLONG tasks → {output}")
    for tt, n in sorted(types.items()):
        print(f"  {tt}: {n}")


if __name__ == "__main__":
    main()
