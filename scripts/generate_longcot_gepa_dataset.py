#!/usr/bin/env python3
"""Generate a GEPA-compatible JSONL dataset from LongCoT vendor data.

Reads ``vendor/longcot/src/data/{domain}/{difficulty}.json``, extracts the
*prompt* (mapped to ``question``) and *answer*, and writes a JSONL file
suitable for ``fleet_rlm.runtime.quality.datasets.load_dataset_rows`` and
the ``longcot-reasoner`` module's row converter.

The script supports two selection modes:

1. **Stratified slice** (default) — uses ``scripts/benchmarks/longcot_mini_stratified_100.json``
   to pick the exact 20 questions per domain that the pilot benchmark used.
2. **Random sampling** — reads all difficulty files for each domain, shuffles
   with a fixed seed, and samples ``--per-domain`` questions.

Questions with empty or missing answers are skipped.  This means domains such
as ``logic`` (which has no reference answers in the vendor data) will yield
zero rows unless a separate answer source is provided.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DATA_DIR = REPO_ROOT / "vendor" / "longcot" / "src" / "data"
STRATIFIED_SLICE_PATH = REPO_ROOT / "scripts" / "benchmarks" / "longcot_mini_stratified_100.json"

DOMAINS = ["logic", "cs", "chemistry", "chess", "math"]
DIFFICULTIES = ["easy", "medium", "hard"]


def _normalize_answer(answer: Any) -> str:
    """Convert a vendor answer payload to a canonical string."""
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer.strip()
    if isinstance(answer, list):
        # Flatten nested lists and join with comma
        parts = []
        for item in answer:
            if isinstance(item, list):
                parts.extend(str(sub) for sub in item)
            else:
                parts.append(str(item))
        return ", ".join(parts)
    if isinstance(answer, dict):
        return json.dumps(answer, separators=(",", ":"), ensure_ascii=False)
    return str(answer).strip()


def _is_valid_answer(answer: Any) -> bool:
    """Return True when *answer* is non-empty after normalization."""
    normalized = _normalize_answer(answer)
    return bool(normalized) and normalized not in {"None", "[]", "{}"}


def _load_stratified_slice() -> dict[str, list[str]] | None:
    """Load the stratified question-id mapping if it exists."""
    if not STRATIFIED_SLICE_PATH.exists():
        return None
    data = json.loads(STRATIFIED_SLICE_PATH.read_text(encoding="utf-8"))
    return data.get("domains")


def _load_questions_for_domain(domain: str, difficulties: list[str]) -> list[dict[str, Any]]:
    """Load all questions for *domain* across the given *difficulties*."""
    questions: list[dict[str, Any]] = []
    for diff in difficulties:
        path = VENDOR_DATA_DIR / domain / f"{diff}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for q in data.get("questions", []):
            q.setdefault("_difficulty", diff)
            questions.append(q)
    return questions


def _select_questions(
    domain: str,
    difficulties: list[str],
    per_domain: int,
    seed: int,
    stratified_qids: list[str] | None,
) -> list[dict[str, Any]]:
    """Select up to *per_domain* questions for *domain*.

    When *stratified_qids* is provided, attempt to locate each question-id in
    the vendor data.  Otherwise fall back to random sampling.
    """
    if stratified_qids is not None:
        all_questions = _load_questions_for_domain(domain, difficulties)
        qid_map = {q["question_id"]: q for q in all_questions}
        selected: list[dict[str, Any]] = []
        for qid in stratified_qids:
            q = qid_map.get(qid)
            if q is not None and _is_valid_answer(q.get("answer")):
                selected.append(q)
            elif q is not None:
                print(
                    f"  Warning: {domain}/{q.get('_difficulty')} {qid} has empty answer — skipping",
                    file=sys.stderr,
                )
        return selected

    # Random sampling mode
    all_questions = _load_questions_for_domain(domain, difficulties)
    valid = [q for q in all_questions if _is_valid_answer(q.get("answer"))]
    rng = random.Random(seed)
    rng.shuffle(valid)
    return valid[:per_domain]


def generate_dataset(
    domains: list[str],
    difficulties: list[str],
    per_domain: int,
    seed: int,
    use_stratified: bool,
) -> list[dict[str, Any]]:
    """Build the full dataset row list."""
    stratified = _load_stratified_slice() if use_stratified else None
    rows: list[dict[str, Any]] = []

    for domain in domains:
        stratified_qids = stratified.get(domain) if stratified else None
        selected = _select_questions(domain, difficulties, per_domain, seed, stratified_qids)

        if not selected:
            print(
                f"Warning: no valid questions for domain '{domain}' — "
                f"the domain will be omitted from the dataset.",
                file=sys.stderr,
            )
            continue

        if len(selected) < per_domain:
            print(
                f"Warning: domain '{domain}' only has {len(selected)} valid questions "
                f"(requested {per_domain}).",
                file=sys.stderr,
            )

        for q in selected:
            rows.append(
                {
                    "question_id": q["question_id"],
                    "domain": domain,
                    "difficulty": q.get("_difficulty", "unknown"),
                    "question": q["prompt"],
                    "answer": _normalize_answer(q.get("answer")),
                }
            )

    return rows


def write_jsonl(rows: list[dict[str, Any]], output_path: Path) -> None:
    """Write *rows* as JSONL to *output_path*, creating parent dirs if needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a GEPA-compatible JSONL dataset from LongCoT vendor data."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "output" / "longcot-eval" / "longcot_gepa_dataset.jsonl",
        help="Output JSONL file path.",
    )
    parser.add_argument(
        "--domains",
        nargs="+",
        default=DOMAINS,
        help="Domains to include (default: all).",
    )
    parser.add_argument(
        "--difficulties",
        nargs="+",
        default=DIFFICULTIES,
        help="Difficulties to include (default: all).",
    )
    parser.add_argument(
        "--per-domain",
        type=int,
        default=20,
        help="Number of questions to sample per domain (default: 20).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for sampling (default: 42).",
    )
    parser.add_argument(
        "--no-stratified",
        action="store_true",
        help="Disable stratified-slice selection; use random sampling instead.",
    )
    args = parser.parse_args(argv)

    rows = generate_dataset(
        domains=args.domains,
        difficulties=args.difficulties,
        per_domain=args.per_domain,
        seed=args.seed,
        use_stratified=not args.no_stratified,
    )

    write_jsonl(rows, args.output)
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
