#!/usr/bin/env python3
"""RLM capabilities evaluation harness.

Compares single-pass (delegate_to_rlm) vs multi-pass (recursive_workspace)
execution on a benchmark dataset, scoring both with coverage and reasoning
quality metrics.

Usage:
    uv run python scripts/evaluate_rlm_capabilities.py \
        --dataset .data/datasets/rlm-recursive-workspace-benchmark.json \
        --output-dir output/rlm-eval

Requires:
    - DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY) set
    - DAYTONA_API_KEY and DAYTONA_API_URL set (for sandbox execution)
    - Optional: MLFLOW_TRACKING_URI for experiment logging
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure src/ is importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger("rlm_eval")


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_benchmark(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Benchmark dataset not found: {path}. "
            "Provide --dataset with a valid JSON benchmark file."
        )
    if not path.is_file():
        raise ValueError(f"Benchmark dataset path is not a file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Benchmark dataset is not valid JSON: {path}") from exc
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def coverage_score(
    answer: str, expected_contains: list[str]
) -> tuple[float, list[str], list[str]]:
    """Score based on how many expected keywords appear in the answer."""
    if not expected_contains:
        return 1.0, [], []
    answer_lower = answer.lower()
    found = [kw for kw in expected_contains if kw.lower() in answer_lower]
    missing = [kw for kw in expected_contains if kw.lower() not in answer_lower]
    score = len(found) / len(expected_contains) if expected_contains else 1.0
    return score, found, missing


def length_quality_signal(answer: str) -> dict[str, Any]:
    """Basic quality signals from answer length."""
    char_count = len(answer)
    word_count = len(answer.split())
    return {
        "char_count": char_count,
        "word_count": word_count,
        "is_substantive": char_count > 100,
        "is_detailed": word_count > 200,
    }


# ---------------------------------------------------------------------------
# Single-pass execution
# ---------------------------------------------------------------------------


def run_single_pass(task: dict[str, Any], interpreter: Any) -> dict[str, Any]:
    """Execute a task with delegate_to_rlm (single child RLM)."""
    from fleet_rlm.runtime.tools.rlm_delegate import (
        _delegate_interpreter,
        delegate_to_rlm,
        set_delegate_interpreter,
    )

    query = task["user_request"]
    started_at = time.time()

    token = set_delegate_interpreter(interpreter)
    try:
        result = delegate_to_rlm(query=query, context="")
    finally:
        _delegate_interpreter.reset(token)

    elapsed_ms = int((time.time() - started_at) * 1000)

    return {
        "mode": "single_pass",
        "task_id": task["id"],
        "status": result.get("status", "error"),
        "answer": result.get("answer", result.get("error", "")),
        "elapsed_ms": elapsed_ms,
        "passes": 1,
    }


# ---------------------------------------------------------------------------
# Multi-pass execution
# ---------------------------------------------------------------------------


def build_workspace_module(interpreter: Any) -> Any:
    """Create the shared RecursiveWorkspaceModule used for workspace benchmarks."""
    from fleet_rlm.runtime.models.builders import RecursiveWorkspaceModule

    return RecursiveWorkspaceModule(
        interpreter=interpreter,
        max_passes=3,
        max_repair_attempts=1,
        subquery_budget=3,
        verbose=False,
        sub_lm=getattr(interpreter, "sub_lm", None),
    )


def run_multi_pass(task: dict[str, Any], module: Any) -> dict[str, Any]:
    """Execute a task with RecursiveWorkspaceModule (multi-pass orchestrator)."""
    query = task["user_request"]
    started_at = time.time()

    try:
        prediction = module(user_request=query, context="")
        answer = str(getattr(prediction, "answer", ""))
        status = str(getattr(prediction, "status", "ok"))
        passes = int(getattr(prediction, "passes", 0))
    except Exception as exc:
        answer = f"Error: {exc}"
        status = "error"
        passes = 0

    elapsed_ms = int((time.time() - started_at) * 1000)

    return {
        "mode": "multi_pass",
        "task_id": task["id"],
        "status": status,
        "answer": answer,
        "elapsed_ms": elapsed_ms,
        "passes": passes,
    }


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------


def evaluate_task(
    task: dict[str, Any],
    interpreter: Any,
    *,
    run_single: bool = True,
    run_multi: bool = True,
    workspace_module: Any | None = None,
) -> dict[str, Any]:
    """Run both modes on a single task and score them."""
    if not run_single and not run_multi:
        raise ValueError("At least one of run_single or run_multi must be enabled")
    task_id = task["id"]
    expected = task.get("expected_answer_contains", [])
    difficulty = task.get("difficulty", "unknown")

    logger.info(
        "Evaluating task %s (%s, difficulty=%s)", task_id, task["task"], difficulty
    )

    result: dict[str, Any] = {
        "task_id": task_id,
        "task_type": task["task"],
        "difficulty": difficulty,
        "requires_multi_pass": task.get("requires_multi_pass", False),
    }

    if run_single:
        single = run_single_pass(task, interpreter)
        single_coverage, single_found, single_missing = coverage_score(
            single["answer"], expected
        )
        single["coverage_score"] = single_coverage
        single["found_keywords"] = single_found
        single["missing_keywords"] = single_missing
        single["quality"] = length_quality_signal(single["answer"])
        result["single_pass"] = single

        logger.info(
            "  single_pass: coverage=%.2f, elapsed=%dms, status=%s",
            single_coverage,
            single["elapsed_ms"],
            single["status"],
        )

    if run_multi:
        module = workspace_module or build_workspace_module(interpreter)
        multi = run_multi_pass(task, module)
        multi_coverage, multi_found, multi_missing = coverage_score(
            multi["answer"], expected
        )
        multi["coverage_score"] = multi_coverage
        multi["found_keywords"] = multi_found
        multi["missing_keywords"] = multi_missing
        multi["quality"] = length_quality_signal(multi["answer"])
        result["multi_pass"] = multi

        logger.info(
            "  multi_pass:  coverage=%.2f, elapsed=%dms, passes=%d, status=%s",
            multi_coverage,
            multi["elapsed_ms"],
            multi["passes"],
            multi["status"],
        )

    if "single_pass" in result and "multi_pass" in result:
        result["coverage_delta"] = (
            result["multi_pass"]["coverage_score"]
            - result["single_pass"]["coverage_score"]
        )
        result["multi_pass_better"] = (
            result["multi_pass"]["coverage_score"]
            > result["single_pass"]["coverage_score"]
        )

    return result


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate statistics across all evaluated tasks."""
    single_scores = [
        r["single_pass"]["coverage_score"] for r in results if "single_pass" in r
    ]
    multi_scores = [
        r["multi_pass"]["coverage_score"] for r in results if "multi_pass" in r
    ]

    hard_tasks = [r for r in results if r.get("requires_multi_pass")]
    easy_tasks = [r for r in results if not r.get("requires_multi_pass")]

    def _avg(scores: list[float]) -> float:
        return sum(scores) / len(scores) if scores else 0.0

    summary = {
        "total_tasks": len(results),
        "single_pass_tasks": len(single_scores),
        "multi_pass_tasks": len(multi_scores),
        "single_pass_avg_coverage": _avg(single_scores),
        "multi_pass_avg_coverage": _avg(multi_scores),
        "multi_pass_wins": sum(1 for r in results if r.get("multi_pass_better", False)),
        "hard_tasks": {
            "count": len(hard_tasks),
            "single_avg": _avg(
                [
                    r["single_pass"]["coverage_score"]
                    for r in hard_tasks
                    if "single_pass" in r
                ]
            ),
            "multi_avg": _avg(
                [
                    r["multi_pass"]["coverage_score"]
                    for r in hard_tasks
                    if "multi_pass" in r
                ]
            ),
        },
        "easy_tasks": {
            "count": len(easy_tasks),
            "single_avg": _avg(
                [
                    r["single_pass"]["coverage_score"]
                    for r in easy_tasks
                    if "single_pass" in r
                ]
            ),
            "multi_avg": _avg(
                [
                    r["multi_pass"]["coverage_score"]
                    for r in easy_tasks
                    if "multi_pass" in r
                ]
            ),
        },
        "total_single_elapsed_ms": sum(
            r["single_pass"]["elapsed_ms"] for r in results if "single_pass" in r
        ),
        "total_multi_elapsed_ms": sum(
            r["multi_pass"]["elapsed_ms"] for r in results if "multi_pass" in r
        ),
    }
    return summary


# ---------------------------------------------------------------------------
# MLflow logging (optional)
# ---------------------------------------------------------------------------


def log_to_mlflow(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    output_dir: Path,
    *,
    results_filename: str,
    summary_filename: str,
) -> None:
    """Log evaluation results to MLflow if configured."""
    try:
        import mlflow

        from fleet_rlm.integrations.observability.mlflow_runtime import (
            get_mlflow_config,
            initialize_mlflow,
        )

        config = get_mlflow_config()
        if not initialize_mlflow(config):
            logger.info("MLflow not configured — skipping experiment logging")
            return

        experiment_name = f"{config.experiment}/rlm-capabilities-eval"
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(
            run_name=f"rlm-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
        ):
            mlflow.log_params(
                {
                    "total_tasks": summary["total_tasks"],
                    "single_pass_tasks": summary.get("single_pass_tasks", 0),
                    "multi_pass_tasks": summary.get("multi_pass_tasks", 0),
                    "hard_tasks": summary["hard_tasks"]["count"],
                    "easy_tasks": summary["easy_tasks"]["count"],
                }
            )
            mlflow.log_metrics(
                {
                    "single_pass_avg_coverage": summary["single_pass_avg_coverage"],
                    "multi_pass_avg_coverage": summary["multi_pass_avg_coverage"],
                    "multi_pass_wins": summary["multi_pass_wins"],
                    "hard_single_avg": summary["hard_tasks"]["single_avg"],
                    "hard_multi_avg": summary["hard_tasks"]["multi_avg"],
                    "easy_single_avg": summary["easy_tasks"]["single_avg"],
                    "easy_multi_avg": summary["easy_tasks"]["multi_avg"],
                }
            )
            mlflow.log_artifact(str(output_dir / results_filename))
            mlflow.log_artifact(str(output_dir / summary_filename))

        logger.info("MLflow experiment logged: %s", experiment_name)
    except Exception as exc:
        logger.warning("MLflow logging failed: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S-NIAH benchmark runner
# ---------------------------------------------------------------------------


def _run_rlm_on_interpreter(
    interpreter: Any, prompt: str, context: str
) -> dict[str, Any]:
    """Execute a single dspy.RLM query directly on the parent interpreter.

    This matches the paper's evaluation setup (depth=1, no child sandbox).
    """
    from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm

    rlm = build_recursive_subquery_rlm(
        interpreter=interpreter,
        max_iterations=10,
        max_llm_calls=20,
        verbose=False,
        sub_lm=getattr(interpreter, "sub_lm", None),
    )
    try:
        prediction = rlm(prompt=prompt, context=context)
        answer = getattr(prediction, "answer", None)
        if answer is None:
            return {"status": "error", "answer": "", "error": "No SUBMIT(answer=...)"}
        return {"status": "ok", "answer": str(answer)}
    except Exception as exc:
        return {"status": "error", "answer": "", "error": str(exc)}


def run_sniah_benchmark(
    interpreter: Any,
    *,
    task_ids: list[str] | None = None,
    max_tasks: int | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the S-NIAH (needle-in-a-haystack) benchmark suite."""
    from benchmarks.sniah import (
        aggregate_sniah_results,
        generate_sniah_dataset,
        score_sniah,
    )

    dataset_path = ROOT / ".data/datasets/sniah-benchmark.json"
    if dataset_path.exists():
        tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
    else:
        tasks = generate_sniah_dataset()

    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    logger.info("S-NIAH: running %d tasks", len(tasks))

    results: list[dict[str, Any]] = []
    for task in tasks:
        started_at = time.time()
        result = _run_rlm_on_interpreter(
            interpreter,
            prompt=task["question"],
            context=task["context"],
        )
        elapsed_ms = int((time.time() - started_at) * 1000)

        answer = result.get("answer", "")
        score = score_sniah(answer, task["expected_answer"])
        results.append(
            {
                "task_id": task["id"],
                "score": score,
                "answer_preview": answer[:200],
                "expected": task["expected_answer"],
                "needle_depth": task["needle_depth"],
                "needle_type": task["needle_type"],
                "haystack_target_chars": task.get(
                    "haystack_target_chars", task["haystack_chars"]
                ),
                "haystack_chars": task["haystack_chars"],
                "elapsed_ms": elapsed_ms,
                "status": result.get("status", "error"),
            }
        )
        logger.info(
            "  %s: score=%.2f elapsed=%dms (depth=%.2f, type=%s)",
            task["id"],
            score,
            elapsed_ms,
            task["needle_depth"],
            task["needle_type"],
        )

    summary = aggregate_sniah_results(results)
    (output_dir / "sniah-results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "sniah-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------
# OOLONG benchmark runner
# ---------------------------------------------------------------------------


def run_oolong_benchmark(
    interpreter: Any,
    *,
    task_ids: list[str] | None = None,
    max_tasks: int | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the OOLONG-style aggregation benchmark suite."""
    from benchmarks.oolong import (
        aggregate_oolong_results,
        generate_oolong_dataset,
        score_oolong_task,
    )

    dataset_path = ROOT / ".data/datasets/oolong-benchmark.json"
    if dataset_path.exists():
        tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
    else:
        tasks = generate_oolong_dataset()

    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    logger.info("OOLONG: running %d tasks", len(tasks))

    results: list[dict[str, Any]] = []
    for task in tasks:
        started_at = time.time()
        result = _run_rlm_on_interpreter(
            interpreter,
            prompt=task["question"],
            context=task["context"],
        )
        elapsed_ms = int((time.time() - started_at) * 1000)

        answer = result.get("answer", result.get("error", ""))
        score = score_oolong_task(task, answer)
        results.append(
            {
                "task_id": task["id"],
                "task_type": task["task_type"],
                "score": score,
                "answer_preview": answer[:200],
                "expected": task["expected_answer"],
                "elapsed_ms": elapsed_ms,
                "status": result.get("status", "error"),
            }
        )
        logger.info(
            "  %s (%s): score=%.4f elapsed=%dms",
            task["id"],
            task["task_type"],
            score,
            elapsed_ms,
        )

    summary = aggregate_oolong_results(results)
    (output_dir / "oolong-results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "oolong-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate RLM capabilities: single-pass vs multi-pass."
    )
    parser.add_argument(
        "--benchmark",
        choices=["workspace", "sniah", "oolong", "all"],
        default="workspace",
        help="Benchmark suite to run (default: workspace)",
    )
    parser.add_argument(
        "--dataset",
        default=str(ROOT / ".data/datasets/rlm-recursive-workspace-benchmark.json"),
    )
    parser.add_argument("--output-dir", default=str(ROOT / "output/rlm-eval"))
    parser.add_argument(
        "--task-ids",
        nargs="*",
        help="Run only specific task IDs (default: all)",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Cap the number of tasks per benchmark (default: no cap)",
    )
    parser.add_argument(
        "--single-only",
        action="store_true",
        help="Run single-pass only (skip multi-pass for quick baseline)",
    )
    parser.add_argument(
        "--multi-only",
        action="store_true",
        help="Run multi-pass only (skip single-pass)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.single_only and args.multi_only:
        parser.error("--single-only and --multi-only cannot be used together")
    return args


def _init_interpreter() -> Any:
    """Configure DSPy and build a Daytona interpreter."""
    from fleet_rlm.runtime.config import configure_planner_from_env

    if not configure_planner_from_env():
        logger.error("Planner LM not configured. Set DSPY_LM_MODEL + DSPY_LLM_API_KEY.")
        sys.exit(1)

    try:
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

        interpreter = DaytonaInterpreter()
        logger.info("Daytona interpreter initialized")
    except Exception as exc:
        logger.error("Daytona interpreter unavailable: %s", exc)
        sys.exit(1)

    logger.info("Warming up Daytona sandbox (image build may take ~30s)...")
    warm_start = time.time()
    try:
        interpreter.execute("SUBMIT(status='warm')")
        logger.info("Warm-up complete in %.1fs", time.time() - warm_start)
    except Exception as exc:
        logger.warning(
            "Warm-up failed after %.1fs: %s (continuing anyway)",
            time.time() - warm_start,
            exc,
        )
    return interpreter


def _run_workspace_benchmark(
    args: argparse.Namespace, interpreter: Any, output_dir: Path
) -> None:
    """Run the workspace (L4 orchestrator) benchmark."""
    dataset_path = Path(args.dataset)
    tasks = load_benchmark(dataset_path)
    if args.task_ids:
        tasks = [t for t in tasks if t["id"] in args.task_ids]
    if args.max_tasks is not None:
        tasks = tasks[: args.max_tasks]
    logger.info("Workspace: loaded %d tasks from %s", len(tasks), dataset_path)

    run_single = not args.multi_only
    run_multi = not args.single_only
    workspace_module = build_workspace_module(interpreter) if run_multi else None

    results: list[dict[str, Any]] = []
    for task in tasks:
        try:
            result = evaluate_task(
                task,
                interpreter,
                run_single=run_single,
                run_multi=run_multi,
                workspace_module=workspace_module,
            )
            results.append(result)
        except Exception as exc:
            logger.error("Task %s failed: %s", task["id"], exc, exc_info=True)
            results.append({"task_id": task["id"], "error": str(exc)})

    (output_dir / "workspace-results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    summary = aggregate_results([r for r in results if "error" not in r])
    (output_dir / "workspace-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log_to_mlflow(
        results,
        summary,
        output_dir,
        results_filename="workspace-results.json",
        summary_filename="workspace-summary.json",
    )

    print("\n--- Workspace Benchmark ---")
    print(f"Tasks: {summary['total_tasks']}")
    if summary["single_pass_tasks"]:
        print(f"Single-pass avg: {summary['single_pass_avg_coverage']:.2%}")
    else:
        print("Single-pass avg: n/a")
    if summary["multi_pass_tasks"]:
        print(f"Multi-pass  avg: {summary['multi_pass_avg_coverage']:.2%}")
    else:
        print("Multi-pass  avg: n/a")
    if summary["single_pass_tasks"] and summary["multi_pass_tasks"]:
        print(f"Multi-pass wins: {summary['multi_pass_wins']}/{summary['total_tasks']}")
    else:
        print("Multi-pass wins: n/a")


def _print_sniah_summary(summary: dict[str, Any]) -> None:
    print("\n--- S-NIAH Benchmark (Needle in a Haystack) ---")
    print(f"Tasks: {summary['tasks_total']}")
    print(f"Accuracy: {summary['accuracy']:.2%}")
    if summary.get("by_depth"):
        print("By needle depth:")
        for depth, acc in summary["by_depth"].items():
            print(f"  {depth}: {acc:.2%}")
    if summary.get("by_type"):
        print("By needle type:")
        for ntype, acc in summary["by_type"].items():
            print(f"  {ntype}: {acc:.2%}")


def _print_oolong_summary(summary: dict[str, Any]) -> None:
    print("\n--- OOLONG Benchmark (Aggregation) ---")
    print(f"Tasks: {summary['tasks_total']}")
    print(f"Avg score: {summary['avg_score']:.4f} (paper metric: 0.75^|y-ŷ|)")
    print(
        f"Perfect: {summary.get('perfect_scores', 0)}, "
        f"Near-miss: {summary.get('near_miss', 0)}, "
        f"Failures: {summary.get('failures', 0)}"
    )
    if summary.get("by_type"):
        print("By task type:")
        for ttype, score in summary["by_type"].items():
            print(f"  {ttype}: {score:.4f}")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    interpreter = _init_interpreter()
    benchmark = args.benchmark

    print("=" * 60)
    print("Fleet-RLM Capabilities Evaluation")
    print(f"Benchmark: {benchmark}")
    print("=" * 60)

    if benchmark in ("sniah", "all"):
        sniah_summary = run_sniah_benchmark(
            interpreter,
            task_ids=args.task_ids,
            max_tasks=args.max_tasks,
            output_dir=output_dir,
        )
        _print_sniah_summary(sniah_summary)

    if benchmark in ("oolong", "all"):
        oolong_summary = run_oolong_benchmark(
            interpreter,
            task_ids=args.task_ids,
            max_tasks=args.max_tasks,
            output_dir=output_dir,
        )
        _print_oolong_summary(oolong_summary)

    if benchmark in ("workspace", "all"):
        _run_workspace_benchmark(args, interpreter, output_dir)

    print("\n" + "=" * 60)
    print(f"Results written to: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
