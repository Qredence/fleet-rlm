"""Shared GEPA optimization pipeline for worker-native DSPy modules.

The core compile→evaluate→save→manifest flow extracted from the per-module
entrypoints.  This runner does **not** force MLflow coupling — the API wrapper
in ``gepa_optimization.py`` engages tracking when appropriate.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

from .artifacts import build_manifest, resolve_artifact_path, write_manifest
from .datasets import (
    load_dataset_rows,
    split_examples_with_metadata,
    validate_required_keys,
    validation_range_for_indexes,
)
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
    baseline_validation_score: float | None
    validation_score: float | None
    output_path: str
    manifest_path: str
    optimizer: str
    program_spec: str
    module_slug: str
    evaluation_results: list[dict[str, Any]]
    prompt_snapshots: list[dict[str, str]]
    review_bundle: dict[str, Any]
    run_metadata: dict[str, Any]


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


def _resolve_model_name(lm: Any) -> str:
    """Return a stable model identifier for a DSPy LM-like object."""
    for attr in ("model", "model_name"):
        value = getattr(lm, attr, None)
        if value:
            return str(value)
    return "unknown"


def _reflection_lm_provenance(reflection_lm: Any) -> dict[str, str]:
    """Describe which reflection LM was selected and why."""
    delegate_model = (os.environ.get("DSPY_DELEGATE_LM_MODEL") or "").strip()
    planner_model = (os.environ.get("DSPY_LM_MODEL") or "").strip()
    resolved_model = _resolve_model_name(reflection_lm)
    source = "delegate" if delegate_model and resolved_model == delegate_model else "planner"
    provenance = {
        "model": resolved_model,
        "source": source,
    }
    if delegate_model:
        provenance["configured_delegate_model"] = delegate_model
    if planner_model:
        provenance["configured_planner_model"] = planner_model
    return provenance


def _capture_prompt_snapshots(module: Any, prompt_type: str) -> list[dict[str, str]]:
    """Extract prompt instructions from all named predictors in a DSPy module.

    Uses DSPy's stable predictor inspection API: ``module.named_predictors()``
    returns ``(name, predictor)`` pairs. Each predictor has
    ``predictor.signature.instructions``.

    Best-effort: returns an empty list on any failure so the optimization
    pipeline is never interrupted by inspection errors.
    """
    snapshots: list[dict[str, str]] = []
    try:
        for name, predictor in module.named_predictors():
            instructions = getattr(getattr(predictor, "signature", None), "instructions", None)
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

    def _serialize_inputs(example: Any) -> str:
        if not hasattr(example, "inputs"):
            return "{}"
        return json.dumps(dict(example.inputs()), default=str)

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
                    "input_data": _serialize_inputs(example),
                    "expected_output": str(getattr(example, "answer", None) or getattr(example, "output", None) or ""),
                    "predicted_output": str(prediction) if prediction else "",
                    "score": score,
                }
            )
        except Exception as exc:
            logger.warning(
                "Per-example evaluation failed for validation example %s",
                idx,
                exc_info=True,
            )
            expected_output = getattr(example, "answer", None) or getattr(example, "output", None) or ""
            results.append(
                {
                    "example_index": idx,
                    "input_data": _serialize_inputs(example),
                    "expected_output": str(expected_output),
                    "predicted_output": f"{exc.__class__.__name__}: {exc}",
                    "score": 0.0,
                }
            )
    return results


def _mean_score(results: list[dict[str, Any]]) -> float | None:
    """Return the arithmetic mean score for a non-empty result list."""
    if not results:
        return None
    return sum(float(item.get("score", 0.0)) for item in results) / len(results)


def _match_prompt_snapshot_pairs(
    before_snapshots: list[dict[str, str]],
    after_snapshots: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Return matched before/after prompt snapshots for shared predictors."""
    before_by_name = {
        snapshot["predictor_name"]: snapshot["prompt_text"]
        for snapshot in before_snapshots
        if snapshot.get("predictor_name") and snapshot.get("prompt_text")
    }
    after_by_name = {
        snapshot["predictor_name"]: snapshot["prompt_text"]
        for snapshot in after_snapshots
        if snapshot.get("predictor_name") and snapshot.get("prompt_text")
    }
    shared = sorted(set(before_by_name) & set(after_by_name))
    return [
        {
            "predictor_name": predictor_name,
            "before_prompt": before_by_name[predictor_name],
            "after_prompt": after_by_name[predictor_name],
        }
        for predictor_name in shared
    ]


def _build_holdout_comparisons(
    *,
    dataset_indexes: list[int],
    baseline_results: list[dict[str, Any]],
    optimized_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build same-split baseline-versus-optimized holdout rows for review."""
    comparisons: list[dict[str, Any]] = []
    total_rows = max(len(dataset_indexes), len(baseline_results), len(optimized_results))
    for offset in range(total_rows):
        baseline_row = baseline_results[offset] if offset < len(baseline_results) else {}
        optimized_row = optimized_results[offset] if offset < len(optimized_results) else {}
        shared_row = optimized_row or baseline_row
        comparisons.append(
            {
                "validation_example_index": int(
                    shared_row.get("example_index", baseline_row.get("example_index", offset))
                ),
                "dataset_row_index": (dataset_indexes[offset] if offset < len(dataset_indexes) else None),
                "input_data": shared_row.get("input_data") or baseline_row.get("input_data") or "{}",
                "expected_output": shared_row.get("expected_output") or baseline_row.get("expected_output") or "",
                "baseline": {
                    "predicted_output": baseline_row.get("predicted_output", ""),
                    "score": float(baseline_row.get("score", 0.0)),
                },
                "optimized": {
                    "predicted_output": optimized_row.get("predicted_output", ""),
                    "score": float(optimized_row.get("score", 0.0)),
                },
            }
        )
    return comparisons


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
    split = split_examples_with_metadata(examples, train_ratio=train_ratio)
    trainset, valset = split.train, split.validation

    # 5. Build metric
    metric = spec.metric_builder()

    # 6. Compile — GEPA requires reflection_lm for prompt evolution
    program = spec.module_factory()
    before_snapshots = _capture_prompt_snapshots(program, "before")
    validation_dataset_indexes = split.validation_indexes
    baseline_results = _evaluate_per_example(program, valset, metric) if len(valset) >= _MIN_VAL_EXAMPLES else []
    baseline_validation_score = _mean_score(baseline_results)

    reflection_lm = _resolve_reflection_lm()
    reflection_provenance = _reflection_lm_provenance(reflection_lm)
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
            validation_score = _mean_score(per_example_results)
        else:
            # Fallback to aggregate evaluator if per-example returned nothing
            validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))
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
    prompt_snapshot_pairs = _match_prompt_snapshot_pairs(
        before_snapshots,
        after_snapshots,
    )
    holdout_comparisons = _build_holdout_comparisons(
        dataset_indexes=validation_dataset_indexes,
        baseline_results=baseline_results,
        optimized_results=per_example_results,
    )
    artifact_metadata = {
        "path": str(resolved_path),
        "manifest_path": str(manifest_path),
        "filename": resolved_path.name,
        "size_bytes": resolved_path.stat().st_size,
        "loader": "dspy.Module.load",
    }
    review_bundle = {
        "version": 1,
        "artifact": artifact_metadata,
        "holdout": {
            "split_reference": {
                "train_ratio": train_ratio,
                "strategy": split.strategy,
                "stratify_by": split.stratify_by,
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "train_dataset_indexes": split.train_indexes,
                "validation_dataset_indexes": validation_dataset_indexes,
                "validation_range": validation_range_for_indexes(validation_dataset_indexes),
                "strata": split.strata,
            },
            "baseline_score": baseline_validation_score,
            "optimized_score": validation_score,
            "score_delta": (
                round(validation_score - baseline_validation_score, 4)
                if validation_score is not None and baseline_validation_score is not None
                else None
            ),
            "comparisons": holdout_comparisons,
        },
        "prompt_snapshots": {
            "matched_predictors": prompt_snapshot_pairs,
            "total_snapshots": len(before_snapshots) + len(after_snapshots),
        },
        "reflection_model": reflection_provenance,
    }
    run_metadata = {
        "module_slug": spec.module_slug,
        "dataset_path": str(dataset_path),
        "review_bundle": review_bundle,
    }
    manifest_data = build_manifest(
        module_spec=spec.program_spec,
        dataset_path=dataset_path,
        train_count=len(trainset),
        val_count=len(valset),
        validation_score=validation_score,
        optimizer="GEPA",
        metric_name=spec.metric_name or None,
        auto=auto,
        extra_metadata={
            "module_slug": spec.module_slug,
            "output_path": str(resolved_path),
            "artifact": artifact_metadata,
            "review_bundle": review_bundle,
        },
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
        baseline_validation_score=baseline_validation_score,
        validation_score=validation_score,
        output_path=str(resolved_path),
        manifest_path=str(manifest_path),
        optimizer="GEPA",
        program_spec=spec.program_spec,
        module_slug=spec.module_slug,
        evaluation_results=per_example_results,
        prompt_snapshots=before_snapshots + after_snapshots,
        review_bundle=review_bundle,
        run_metadata=run_metadata,
    )
