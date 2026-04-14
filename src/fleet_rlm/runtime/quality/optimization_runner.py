"""Shared GEPA optimization pipeline for worker-native DSPy modules.

The core compile→evaluate→save→manifest flow extracted from the per-module
entrypoints.  This runner does **not** force MLflow coupling — the API wrapper
in ``gepa_optimization.py`` engages tracking when appropriate.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, TypedDict

from .artifacts import build_manifest, resolve_artifact_path, write_manifest
from .datasets import load_dataset_rows, split_examples, validate_required_keys
from .module_registry import ModuleOptimizationSpec

logger = logging.getLogger(__name__)

# Minimum examples required in the validation set for a meaningful evaluation.
# When the split produces fewer validation examples, the run proceeds without
# a validation score and records that fact in the manifest.
_MIN_VAL_EXAMPLES = 1


class OptimizationResult(TypedDict):
    """Structured summary returned by ``run_module_optimization``."""

    train_examples: int
    validation_examples: int
    validation_score: float | None
    output_path: str
    manifest_path: str
    optimizer: str
    program_spec: str
    module_slug: str


def _persist_run_artifacts(
    run_id: int,
    per_example_results: list[dict[str, Any]],
    snapshots: list[dict[str, str]],
) -> None:
    """Best-effort persistence of evaluation results and prompt snapshots."""
    try:
        from fleet_rlm.integrations.local_store import (
            save_evaluation_results,
            save_prompt_snapshots,
        )

        if per_example_results:
            save_evaluation_results(run_id, per_example_results)
        if snapshots:
            save_prompt_snapshots(run_id, snapshots)
    except Exception:
        logger.warning(
            "Failed to persist evaluation artifacts for run %s — continuing",
            run_id,
            exc_info=True,
        )


def _resolve_reflection_lm() -> Any:
    """Resolve a DSPy LM suitable for GEPA's reflection pass.

    Resolution order:
    1. ``DSPY_DELEGATE_LM_MODEL`` env var (stronger model for reflection)
    2. ``DSPY_LM_MODEL`` env var (primary planner model)
    3. Raises ``RuntimeError`` if no model is configured

    GEPA requires ``reflection_lm`` (or a custom ``instruction_proposer``).
    This helper ensures a concrete LM is always provided.
    """
    from fleet_rlm.runtime.config import (
        get_delegate_lm_from_env,
        get_planner_lm_from_env,
    )

    lm = get_delegate_lm_from_env()
    if lm is not None:
        return lm
    lm = get_planner_lm_from_env()
    if lm is not None:
        return lm
    raise RuntimeError(
        "No DSPy LM configured for GEPA reflection. "
        "Set DSPY_LM_MODEL (and DSPY_LLM_API_KEY) or DSPY_DELEGATE_LM_MODEL "
        "in the environment or a .env file."
    )


def _capture_prompt_snapshots(module: Any, prompt_type: str) -> list[dict[str, str]]:
    """Extract prompt instructions from all named predictors in a DSPy module.

    Uses stable DSPy 3.1.3 API: ``module.named_predictors()`` returns
    ``(name, predictor)`` pairs.  Each predictor has
    ``predictor.signature.instructions``.

    Best-effort: returns an empty list on any failure so the optimization
    pipeline is never interrupted by inspection errors.
    """
    snapshots: list[dict[str, str]] = []
    try:
        for name, predictor in module.named_predictors():
            instructions = getattr(
                getattr(predictor, "signature", None), "instructions", None
            )
            if instructions:
                snapshots.append(
                    {
                        "predictor_name": name,
                        "prompt_type": prompt_type,
                        "prompt_text": str(instructions),
                    }
                )
    except Exception:
        pass  # Best-effort — don't break optimization on inspection failure
    return snapshots


def _evaluate_per_example(
    compiled_module: Any,
    validation_set: list[Any],
    metric_fn: Any,
) -> list[dict[str, Any]]:
    """Evaluate each validation example individually, collecting per-example scores.

    Each example is evaluated in isolation so that a single failure does not
    prevent scoring the remaining examples.
    """
    results: list[dict[str, Any]] = []
    for idx, example in enumerate(validation_set):
        try:
            prediction = compiled_module(**example.inputs())
            raw_score = metric_fn(example, prediction)
            # Unwrap ScoreWithFeedback or similar wrappers
            score = float(getattr(raw_score, "score", raw_score))
            results.append(
                {
                    "example_index": idx,
                    "input_data": json.dumps(dict(example.inputs())),
                    "expected_output": str(
                        getattr(example, "answer", None)
                        or getattr(example, "output", None)
                        or ""
                    ),
                    "predicted_output": str(prediction) if prediction else "",
                    "score": score,
                }
            )
        except Exception:
            results.append(
                {
                    "example_index": idx,
                    "input_data": json.dumps(
                        dict(example.inputs()) if hasattr(example, "inputs") else {}
                    ),
                    "expected_output": "",
                    "predicted_output": "",
                    "score": 0.0,
                }
            )
    return results


def _ensure_dspy_configured() -> None:
    """Ensure DSPy has a global LM configured for module execution.

    If ``dspy.settings.lm`` is already set (e.g. by an outer caller), this is
    a no-op.  Otherwise it falls back to ``configure_planner_from_env()``.
    """
    import dspy
    from fleet_rlm.runtime.config import configure_planner_from_env

    if getattr(dspy.settings, "lm", None) is not None:
        return
    if not configure_planner_from_env():
        raise RuntimeError(
            "DSPy LM is not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY "
            "in the environment before running offline optimization."
        )


def run_module_optimization(
    spec: ModuleOptimizationSpec,
    *,
    dataset_path: str | Path,
    output_path: str | Path | None = None,
    default_output_root: str | Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
    run_id: int | None = None,
) -> OptimizationResult:
    """Run the full offline GEPA optimization pipeline for a registered module.

    Steps:
        1. Ensure DSPy LM is configured
        2. Load and validate the dataset
        3. Convert rows to DSPy examples via the module's row converter
        4. Split into train/val
        5. Build module-specific metric
        6. Compile with GEPA (capture before/after prompt snapshots)
        7. Per-example evaluation on the validation set
        8. Save the optimized artifact
        9. Write manifest
        10. Persist evaluation results and prompt snapshots (when run_id given)
        11. Return a structured summary

    When *run_id* is provided, per-example evaluation results and before/after
    prompt snapshots are persisted to the local store.

    This function is safe to call from CLI or background tasks.
    It does **not** depend on MLflow or any live-request infrastructure.

    Raises:
        RuntimeError: If no DSPy LM is configured (for GEPA reflection or
            module execution).
        ValueError: If the dataset is empty or all rows are malformed.
    """
    import dspy
    from dspy.teleprompt import GEPA

    dataset_path = Path(dataset_path)

    # 1. Ensure DSPy LM is configured for module execution
    _ensure_dspy_configured()

    # 2. Load + validate
    rows = load_dataset_rows(dataset_path)
    valid_rows = validate_required_keys(rows, spec.required_dataset_keys, spec.label)

    # 3. Convert
    examples = spec.row_converter(valid_rows)

    # 4. Split
    trainset, valset = split_examples(examples, train_ratio=train_ratio)

    # 5. Build metric
    metric = spec.metric_builder()

    # 6. Compile — GEPA requires reflection_lm for prompt evolution
    program = spec.module_factory()
    before_snapshots = _capture_prompt_snapshots(program, "before")

    reflection_lm = _resolve_reflection_lm()
    optimizer = GEPA(metric=metric, auto=auto, reflection_lm=reflection_lm)
    optimized = optimizer.compile(
        program,
        trainset=trainset,
        valset=valset if len(valset) >= _MIN_VAL_EXAMPLES else None,
    )

    after_snapshots = _capture_prompt_snapshots(optimized, "after")

    # 7. Per-example evaluation (only when a real validation set exists)
    validation_score: float | None = None
    per_example_results: list[dict[str, Any]] = []
    has_val = len(valset) >= _MIN_VAL_EXAMPLES
    if has_val:
        per_example_results = _evaluate_per_example(optimized, valset, metric)
        if per_example_results:
            validation_score = sum(r["score"] for r in per_example_results) / len(
                per_example_results
            )
        else:
            # Fallback to aggregate evaluator if per-example returned nothing
            validation_score = float(
                dspy.Evaluate(devset=valset, metric=metric)(optimized)
            )
    else:
        logger.warning(
            "Validation split is empty for %s — skipping evaluation. "
            "Provide more examples or a lower --train-ratio for validation scoring.",
            spec.module_slug,
        )

    # 8. Save artifact
    resolved_path = resolve_artifact_path(
        spec.module_slug,
        spec.artifact_filename,
        output_path,
        default_root=default_output_root,
    )
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_path))

    # 9. Write manifest
    manifest_path = resolved_path.with_suffix(".manifest.json")
    manifest_data = build_manifest(
        module_spec=spec.program_spec,
        dataset_path=dataset_path,
        train_count=len(trainset),
        val_count=len(valset),
        validation_score=validation_score,
        optimizer="GEPA",
        metric_name=spec.metric_name or None,
        auto=auto,
    )
    write_manifest(manifest_path, manifest_data)

    # 10. Persist evaluation results and prompt snapshots
    if run_id is not None:
        all_snapshots = before_snapshots + after_snapshots
        _persist_run_artifacts(run_id, per_example_results, all_snapshots)

    # 11. Return summary
    return OptimizationResult(
        train_examples=len(trainset),
        validation_examples=len(valset),
        validation_score=validation_score,
        output_path=str(resolved_path),
        manifest_path=str(manifest_path),
        optimizer="GEPA",
        program_spec=spec.program_spec,
        module_slug=spec.module_slug,
    )
