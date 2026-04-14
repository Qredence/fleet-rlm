"""Shared GEPA optimization pipeline for worker-native DSPy modules.

The core compile→evaluate→save→manifest flow extracted from the per-module
entrypoints.  This runner does **not** force MLflow coupling — the API wrapper
in ``gepa_optimization.py`` engages tracking when appropriate.
"""

from __future__ import annotations

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
) -> OptimizationResult:
    """Run the full offline GEPA optimization pipeline for a registered module.

    Steps:
        1. Ensure DSPy LM is configured
        2. Load and validate the dataset
        3. Convert rows to DSPy examples via the module's row converter
        4. Split into train/val
        5. Build module-specific metric
        6. Compile with GEPA (``reflection_lm`` resolved automatically)
        7. Evaluate on the validation set (skipped when val split is empty)
        8. Save the optimized artifact
        9. Write manifest
        10. Return a structured summary

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
    reflection_lm = _resolve_reflection_lm()
    optimizer = GEPA(metric=metric, auto=auto, reflection_lm=reflection_lm)
    optimized = optimizer.compile(
        program,
        trainset=trainset,
        valset=valset if len(valset) >= _MIN_VAL_EXAMPLES else None,
    )

    # 7. Evaluate (only when a real validation set exists)
    validation_score: float | None = None
    has_val = len(valset) >= _MIN_VAL_EXAMPLES
    if has_val:
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

    # 10. Return summary
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
