#!/usr/bin/env python3
"""Evaluate fleet-rlm against Prime Intellect's official oolong-rlm benchmark.

Uses:
- The official HuggingFace datasets: `oolongbench/oolong-synth`, `oolongbench/oolong-real`
- The official OOLONG scoring logic (ported verbatim from oolong_rlm.py pulled from
  `primeintellect/oolong-rlm` v0.1.9). The `verifiers` package cannot be imported
  directly in our venv due to a broken transitive dependency, so we vendor just the
  scoring functions. Original source:
  https://github.com/PrimeIntellect-ai/research-environments/blob/main/environments/oolong_rlm/oolong_rlm.py

Runs each example through fleet-rlm's DaytonaInterpreter + dspy.RLM.

Usage:
    uv run python scripts/oolong_official_eval.py \\
        --subset synth --split validation --context-len 131072 --limit 10 \\
        --output-dir output/rlm-eval-full/oolong-official
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import dateutil.parser

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Redirect HuggingFace caches to the project SSD before any datasets import
# (home disk is near-full; project SSD has terabytes free)
_HF_CACHE = ROOT / ".data" / "hf-cache"
_HF_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_HF_CACHE))
os.environ.setdefault("HF_DATASETS_CACHE", str(_HF_CACHE / "datasets"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger("oolong_official_eval")


# ---------------------------------------------------------------------------
# Monkey-patch: DSPy v3's _strip_code_fences crashes when the LLM returns an
# action with no code block (reasoning-only response). Patch it to treat None
# as empty string so the RLM loop gracefully continues to the next iteration.
# ---------------------------------------------------------------------------


def _patch_dspy_rlm() -> None:
    try:
        from dspy.predict import rlm as _rlm

        original_strip = _rlm._strip_code_fences

        def _safe_strip_code_fences(code):
            if code is None:
                return ""
            return original_strip(code)

        _rlm._strip_code_fences = _safe_strip_code_fences
        logger.info("Patched dspy.predict.rlm._strip_code_fences for None safety")
    except Exception as exc:
        logger.warning("Failed to patch dspy.predict.rlm: %s", exc)

    try:
        from dspy.primitives import repl_types as _repl_types

        original_append = _repl_types.REPLHistory.append

        def _safe_append(self, *args, **kwargs):
            if len(args) > 3:
                raise TypeError(
                    f"REPLHistory.append accepts at most 3 positional values, got {len(args)}"
                )
            names = ("reasoning", "code", "output")
            merged = dict(kwargs)
            for name, value in zip(names, args):
                merged.setdefault(name, value)
            return original_append(
                self,
                reasoning=merged["reasoning"]
                if isinstance(merged.get("reasoning"), str)
                else "",
                code=merged["code"] if isinstance(merged.get("code"), str) else "",
                output=merged["output"]
                if isinstance(merged.get("output"), str)
                else "",
            )

        _repl_types.REPLHistory.append = _safe_append
        logger.info(
            "Patched dspy.primitives.repl_types.REPLHistory.append for None safety"
        )
    except Exception as exc:
        logger.warning("Failed to patch dspy.primitives.repl_types: %s", exc)


_patch_dspy_rlm()


# ---------------------------------------------------------------------------
# OFFICIAL OOLONG scoring (ported verbatim from oolong_rlm v0.1.9)
# ---------------------------------------------------------------------------


def _synth_attempt_answer_parse(answer: str) -> tuple[str, str]:
    """Parse a model response for the synth subset.

    Returns (parsed_answer, parse_confidence).
    """
    parse_confidence = "low"
    if ":" not in answer:
        if len(answer) < 20:
            return answer, parse_confidence
        else:
            parts = answer.split()
            return parts[-1] if parts else "", parse_confidence
    candidate_answer = answer.split(":")[-1].strip()
    candidate_answer = candidate_answer.replace("*", "")
    candidate_answer = candidate_answer.replace("[", "")
    candidate_answer = candidate_answer.replace("]", "")
    parse_confidence = "med"
    if (
        "User:" in answer
        or "Answer:" in answer
        or "Date:" in answer
        or "Label" in answer
    ):
        parse_confidence = "high"
    if len(candidate_answer) < 20:
        parse_confidence = "vhigh"
    elif "more common" in candidate_answer:
        candidate_answer = "more common"
    elif "less common" in candidate_answer:
        candidate_answer = "less common"
    elif "same frequency" in candidate_answer:
        candidate_answer = "same frequency"
    return candidate_answer, parse_confidence


def _synth_score(answer_raw: str, answer_type: str, output: str) -> float:
    """Score a synth subset response using the official OOLONG scoring logic."""
    try:
        gold = (
            ast.literal_eval(answer_raw)[0]
            if "datetime" not in answer_raw
            else datetime.strptime(answer_raw, "[datetime.date(%Y, %m, %d)]")
        )
    except (ValueError, SyntaxError, TypeError, IndexError):
        return 0.0
    trimmed_output, _ = _synth_attempt_answer_parse(output)

    if str(trimmed_output) == str(gold):
        return 1.0
    elif str(trimmed_output) in ["more common", "less common", "same frequency"]:
        if str(trimmed_output) in str(gold):
            return 1.0
    elif answer_type == "ANSWER_TYPE.NUMERIC":
        try:
            return float(0.75 ** abs(int(gold) - int(trimmed_output)))
        except (TypeError, ValueError):
            pass
    elif answer_type == "ANSWER_TYPE.DATE":
        try:
            parsed = dateutil.parser.parse(str(trimmed_output))
            return 1.0 if parsed == gold else 0.0
        except (OverflowError, TypeError, ValueError):
            pass
    return 0.0


def _dnd_parse_answer(answer: str) -> int | str | list[str]:
    """Parse a DnD gold/model answer into int, str, or list of str."""
    try:
        return int(answer)
    except ValueError:
        pass
    if "," in answer:
        return [item.strip() for item in answer.split(",") if item.strip()]
    return answer


def _dnd_score(answer_raw: str, output: str) -> float:
    """Score a DnD subset response using the official OOLONG scoring logic."""
    gold = _dnd_parse_answer(answer_raw)
    trimmed_output = _dnd_parse_answer((output or "").strip())

    if isinstance(gold, int) and isinstance(trimmed_output, int):
        return float(0.75 ** abs(gold - trimmed_output))
    elif isinstance(gold, str) and isinstance(trimmed_output, str):
        return 1.0 if gold.strip().lower() == trimmed_output.strip().lower() else 0.0
    elif isinstance(gold, list) and isinstance(trimmed_output, list):
        overlap = set(gold) & set(trimmed_output)
        return len(overlap) / len(gold) if gold else 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Fleet-RLM runner
# ---------------------------------------------------------------------------


def run_rlm_task(
    interpreter: Any,
    prompt: str,
    context: str,
    *,
    max_iterations: int = 10,
    max_llm_calls: int = 20,
) -> dict[str, Any]:
    """Run a single task through fleet-rlm's dspy.RLM on the given interpreter."""
    from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm

    rlm = build_recursive_subquery_rlm(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=False,
        sub_lm=getattr(interpreter, "sub_lm", None),
    )
    started_at = time.time()
    try:
        prediction = rlm(prompt=prompt, context=context)
        answer = getattr(prediction, "answer", None)
        status = "ok" if answer is not None else "error_no_submit"
        answer = str(answer) if answer is not None else ""
    except Exception as exc:
        status = "error"
        answer = ""
        logger.warning("RLM execution failed: %s", exc)
    elapsed_ms = int((time.time() - started_at) * 1000)
    return {"status": status, "answer": answer, "elapsed_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# Dataset loading + evaluation loop
# ---------------------------------------------------------------------------


def load_official_dataset(
    subset: str,
    split: str,
    dataset_name: str | None,
    context_len: int | None,
) -> Any:
    """Load the official OOLONG dataset from HuggingFace."""
    from datasets import load_dataset

    if subset == "real":
        hf_dataset = "oolongbench/oolong-real"
        config = dataset_name or "dnd"
        ds = load_dataset(hf_dataset, config, split=split)
    else:
        hf_dataset = "oolongbench/oolong-synth"
        ds = load_dataset(hf_dataset, split=split)
        if dataset_name:
            ds = ds.filter(lambda x: x.get("dataset") == dataset_name)
        if context_len:
            ds = ds.filter(lambda x: x.get("context_len") == context_len)
    return ds


def evaluate(
    *,
    subset: str,
    split: str,
    dataset_name: str | None,
    context_len: int | None,
    limit: int,
    output_dir: Path,
) -> dict[str, Any]:
    ds = load_official_dataset(subset, split, dataset_name, context_len)
    total_available = len(ds)
    n = min(limit, total_available)
    logger.info(
        "Loaded %d examples from oolongbench/oolong-%s (%s); evaluating %d",
        total_available,
        subset,
        split,
        n,
    )

    # Configure fleet-rlm interpreter
    from fleet_rlm.runtime.config import configure_planner_from_env

    if not configure_planner_from_env():
        logger.error("Planner LM not configured. Set DSPY_LM_MODEL + DSPY_LLM_API_KEY.")
        sys.exit(1)

    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    interpreter = DaytonaInterpreter()
    logger.info("Warming up Daytona sandbox...")
    warm_start = time.time()
    interpreter.execute("SUBMIT(status='warm')")
    logger.info("Warm-up complete in %.1fs", time.time() - warm_start)

    context_column = (
        "context_window_text_with_labels"
        if subset == "synth_with_labels"
        else "context_window_text"
    )

    results: list[dict[str, Any]] = []
    for idx in range(n):
        example = ds[idx]
        question = example["question"]
        context = example[context_column]
        answer_raw = example["answer"]
        answer_type = example.get("answer_type", "")

        logger.info(
            "  [%d/%d] dataset=%s context_len=%s type=%s",
            idx + 1,
            n,
            example.get("dataset", example.get("config", "?")),
            example.get("context_len", "?"),
            answer_type,
        )

        result = run_rlm_task(interpreter, question, context)

        if subset == "real":
            score = _dnd_score(answer_raw, result["answer"])
        else:
            score = _synth_score(answer_raw, answer_type, result["answer"])

        entry = {
            "example_id": idx,
            "dataset": example.get("dataset", example.get("config")),
            "context_len": example.get("context_len"),
            "answer_type": answer_type,
            "expected": answer_raw,
            "answer": result["answer"],
            "answer_preview": result["answer"][:200],
            "score": score,
            "status": result["status"],
            "elapsed_ms": result["elapsed_ms"],
        }
        results.append(entry)
        logger.info(
            "    → score=%.4f elapsed=%dms status=%s",
            score,
            result["elapsed_ms"],
            result["status"],
        )

    # Aggregate
    def _avg(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    by_dataset: dict[str, list[float]] = {}
    by_context_len: dict[str, list[float]] = {}
    by_answer_type: dict[str, list[float]] = {}
    for r in results:
        by_dataset.setdefault(str(r["dataset"] or "?"), []).append(r["score"])
        by_context_len.setdefault(str(r["context_len"] or "?"), []).append(r["score"])
        by_answer_type.setdefault(str(r["answer_type"] or "?"), []).append(r["score"])

    summary = {
        "benchmark": "oolong-official",
        "source": "primeintellect/oolong-rlm v0.1.9",
        "subset": subset,
        "split": split,
        "dataset_name": dataset_name,
        "context_len": context_len,
        "tasks_total": len(results),
        "avg_score": round(_avg([r["score"] for r in results]), 4),
        "perfect_scores": sum(1 for r in results if r["score"] >= 0.99),
        "near_miss": sum(1 for r in results if 0.5 <= r["score"] < 0.99),
        "failures": sum(1 for r in results if r["score"] < 0.5),
        "by_dataset": {k: round(_avg(v), 4) for k, v in sorted(by_dataset.items())},
        "by_context_len": {
            k: round(_avg(v), 4) for k, v in sorted(by_context_len.items())
        },
        "by_answer_type": {
            k: round(_avg(v), 4) for k, v in sorted(by_answer_type.items())
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "oolong-official-results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "oolong-official-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate fleet-rlm against the official OOLONG benchmark."
    )
    parser.add_argument(
        "--subset",
        choices=["synth", "synth_with_labels", "real"],
        default="synth",
    )
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="Filter to specific dataset (synth: spam/trec_coarse/agnews/etc; real: dnd/toy_dnd)",
    )
    parser.add_argument(
        "--context-len",
        type=int,
        default=None,
        help="Filter to specific context length (synth only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max number of examples to evaluate",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output/rlm-eval-full/oolong-official"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    summary = evaluate(
        subset=args.subset,
        split=args.split,
        dataset_name=args.dataset_name,
        context_len=args.context_len,
        limit=args.limit,
        output_dir=Path(args.output_dir),
    )

    print("\n" + "=" * 60)
    print("OOLONG Official Benchmark (primeintellect/oolong-rlm v0.1.9)")
    print("=" * 60)
    print(f"Subset: {summary['subset']} / Split: {summary['split']}")
    if summary.get("dataset_name"):
        print(f"Dataset filter: {summary['dataset_name']}")
    if summary.get("context_len"):
        print(f"Context length filter: {summary['context_len']}")
    print(f"Tasks evaluated: {summary['tasks_total']}")
    print(f"Avg score: {summary['avg_score']:.4f} (paper RLM(GPT-5): 0.565)")
    print(
        f"Perfect: {summary['perfect_scores']}, "
        f"Near-miss: {summary['near_miss']}, "
        f"Failures: {summary['failures']}"
    )
    if summary.get("by_dataset"):
        print("\nBy dataset:")
        for k, v in summary["by_dataset"].items():
            print(f"  {k}: {v:.4f}")
    if summary.get("by_context_len"):
        print("\nBy context length:")
        for k, v in summary["by_context_len"].items():
            print(f"  {k}: {v:.4f}")
    print(f"\nResults: {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
