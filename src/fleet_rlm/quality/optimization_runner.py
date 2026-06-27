"""Single offline GEPA optimization pipeline for fleet-rlm DSPy modules.

Dataset → ``dspy.Example`` → GEPA →
``dspy.Evaluate`` → save + manifest. Both registry modules (via
``ModuleOptimizationSpec``) and ad-hoc ``module:attr`` program specs (via
:func:`spec_for_program`) flow through :func:`run_module_optimization`.

This runner does **not** force MLflow coupling — the API background worker
engages tracking when appropriate.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

from .artifacts import build_manifest, resolve_artifact_path, write_manifest
from .datasets import (
    load_dataset_rows,
    rows_to_examples,
    split_examples_with_metadata,
    validate_required_keys,
    validation_range_for_indexes,
)
from .gepa_evidence import write_gepa_evidence_artifact as _write_gepa_evidence_artifact
from .module_registry import ModuleOptimizationSpec
from .optimization_insights import build_manifest_insights as _build_manifest_insights

logger = logging.getLogger(__name__)

OptimizerName = Literal["gepa"]

OPTIMIZER_LABELS: dict[str, str] = {"gepa": "GEPA"}

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
    feedback_summary: str


# ---------------------------------------------------------------------------
# Program building (module:attr specs)
# ---------------------------------------------------------------------------


def load_symbol(spec: str) -> Any:
    """Resolve a ``module:attr`` symbol reference."""
    from importlib import import_module

    if ":" not in spec:
        raise ValueError("Expected symbol in the form 'module:attr'.")
    module_name, attr_name = spec.split(":", 1)
    module = import_module(module_name)
    return getattr(module, attr_name)


def build_program(spec: str) -> Any:
    """Instantiate a DSPy program from a symbol reference."""
    import dspy

    target = load_symbol(spec)
    if isinstance(target, dspy.Module):
        return target
    if isinstance(target, type) and issubclass(target, dspy.Module):
        return target()
    if callable(target):
        program = target()
        if isinstance(program, dspy.Module):
            return program
    raise TypeError(f"Could not build a DSPy module from '{spec}'.")


def build_gepa_feedback_metric(
    *,
    output_key: str = "response",
    score_fn: Callable[..., float | tuple[float, str]] | None = None,
) -> Callable[..., Any]:
    """Build a GEPA-compatible feedback metric.

    When *score_fn* is ``None`` the default
    :func:`~.workspace_metrics.workspace_feedback_metric` is used.  Tuple
    results ``(score, feedback)`` are wrapped into ``ScoreWithFeedback`` so
    GEPA's reflection pass can use the textual explanation.
    """
    from .dspy_evaluation import _metric_supports_trace
    from .workspace_metrics import workspace_feedback_metric

    inner = score_fn
    inner_supports_trace = _metric_supports_trace(inner) if inner is not None else False
    inner_accepts_kwargs = False
    inner_param_names: set[str] = set()
    if inner is not None:
        try:
            inner_params = list(inspect.signature(inner).parameters.values())
            inner_accepts_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in inner_params)
            inner_param_names = {param.name for param in inner_params}
        except (TypeError, ValueError):
            inner_accepts_kwargs = False
            inner_param_names = set()

    def _call_feedback_metric(
        gold: Any,
        pred: Any,
        *,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> float | tuple[float, str]:
        if inner is None:
            return workspace_feedback_metric(gold, pred, trace=trace, output_key=output_key)
        if inner_supports_trace:
            kwargs: dict[str, Any] = {"trace": trace}
            if inner_accepts_kwargs or "pred_name" in inner_param_names:
                kwargs["pred_name"] = pred_name
            if inner_accepts_kwargs or "pred_trace" in inner_param_names:
                kwargs["pred_trace"] = pred_trace
            return inner(gold, pred, **kwargs)
        return inner(gold, pred)

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> Any:
        from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

        result = _call_feedback_metric(gold, pred, trace=trace, pred_name=pred_name, pred_trace=pred_trace)
        if isinstance(result, tuple) and len(result) == 2:
            score, feedback = result
            return ScoreWithFeedback(score=float(score), feedback=str(feedback))
        return float(result)

    return metric


def spec_for_program(
    program_spec: str,
    *,
    input_keys: list[str] | None = None,
    output_key: str = "response",
    score_fn: Callable[..., float | tuple[float, str]] | None = None,
) -> ModuleOptimizationSpec:
    """Build an ad-hoc spec so ``module:attr`` programs use the same pipeline.

    The dataset is expected in the exported MLflow trace-row format
    (``inputs`` + ``expectations.expected_response`` per row).
    """
    slug = "program-" + (re.sub(r"[^a-z0-9]+", "-", program_spec.lower()).strip("-") or "custom")
    return ModuleOptimizationSpec(
        module_slug=slug,
        label=program_spec,
        program_spec=program_spec,
        artifact_filename="optimized_program.json",
        input_keys=list(input_keys or []),
        required_dataset_keys=[],
        module_factory=lambda: build_program(program_spec),
        row_converter=lambda rows: rows_to_examples(rows, input_keys=input_keys, output_key=output_key),
        metric_builder=lambda: build_gepa_feedback_metric(output_key=output_key, score_fn=score_fn),
        metric_name="workspace_feedback_metric" if score_fn is None else "custom_score_fn",
        description=f"Ad-hoc optimization of {program_spec}",
    )


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


def _resolve_reflection_lm(reflection_lm_config: dict[str, Any] | None = None) -> Any:
    """Resolve a DSPy LM suitable for GEPA's reflection pass.

    GEPA requires ``reflection_lm`` (or a custom ``instruction_proposer``).
    Resolution is delegated to ``resolve_lm("reflection")`` (delegate model
    first, planner fallback); this helper ensures a concrete LM is provided.
    """
    if reflection_lm_config:
        import dspy

        lm_kwargs = dict(reflection_lm_config.get("lm_kwargs") or {})
        if not lm_kwargs:
            raise RuntimeError("Selected reflection model is missing DSPy LM configuration.")
        # Use ResponseAPILM for OpenAI providers
        model = lm_kwargs.get("model", "")
        if model.startswith("openai/"):
            from fleet_rlm.runtime.lm import ResponseAPILM

            return ResponseAPILM(**lm_kwargs)
        return dspy.LM(**lm_kwargs)

    from fleet_rlm.runtime.config import resolve_lm

    lm = resolve_lm("reflection")
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


def _reflection_lm_provenance(
    reflection_lm: Any,
    *,
    reflection_lm_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Describe which reflection LM was selected and why."""
    if reflection_lm_config:
        provenance = {
            "model": str(reflection_lm_config.get("model_id") or _resolve_model_name(reflection_lm)),
            "source": "profile",
        }
        for key in ("profile_id", "profile_name", "litellm_model"):
            value = reflection_lm_config.get(key)
            if value:
                provenance[key] = str(value)
        return provenance

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


def _serialize_example_inputs(example: Any) -> str:
    """Serialize a DSPy example's input fields as a JSON string."""
    if not hasattr(example, "inputs"):
        return "{}"
    try:
        return json.dumps(dict(example.inputs()), default=str)
    except Exception:
        return "{}"


def _evaluate_validation_set(
    program: Any,
    validation_set: list[Any],
    metric_fn: Any,
) -> list[dict[str, Any]]:
    """Score the validation set through ``dspy.Evaluate``, one row per example.

    ``dspy.Evaluate`` provides failure isolation (failed examples receive
    ``failure_score=0.0``) and returns ``(example, prediction, score)``
    triples that are reshaped into the persistence row format.
    """
    import dspy

    from .dspy_evaluation import _build_evaluate_metric

    if not validation_set:
        return []

    evaluator = dspy.Evaluate(
        devset=validation_set,
        metric=_build_evaluate_metric(metric_fn),
        num_threads=1,
        display_progress=False,
        failure_score=0.0,
    )
    evaluation = evaluator(program)

    rows: list[dict[str, Any]] = []
    for idx, (example, prediction, raw_score) in enumerate(getattr(evaluation, "results", []) or []):
        score = float(getattr(raw_score, "score", raw_score) or 0.0)
        expected = getattr(example, "answer", None) or getattr(example, "output", None) or ""
        rows.append(
            {
                "example_index": idx,
                "input_data": _serialize_example_inputs(example),
                "expected_output": str(expected),
                "predicted_output": str(prediction) if prediction else "",
                "score": score,
            }
        )
    return rows


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


def _supported_kwargs(callable_obj: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to parameters accepted by *callable_obj*.

    Test doubles often implement only the subset of DSPy's GEPA constructor
    needed by a unit test. Production DSPy accepts the richer GEPA evidence
    capture kwargs, so this helper lets the runner enable them without making
    tests or older installations brittle.
    """
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return kwargs
    parameters = signature.parameters
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in parameters}


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


def _build_feedback_summary(results: list[dict[str, Any]], validation_score: float | None) -> str:
    """Summarize the validation outcome for run metadata."""
    if not results:
        return "Validation feedback was not available because no validation examples were evaluated."
    low_scores = [row for row in results if float(row.get("score", 0.0)) < 0.75]
    if not low_scores:
        return f"All validation examples scored strongly; optimized validation score={validation_score}."
    sample = low_scores[0]
    expected = str(sample.get("expected_output", ""))[:120]
    predicted = str(sample.get("predicted_output", ""))[:120]
    return (
        f"{len(low_scores)} validation example(s) scored below 0.75; "
        f"optimized validation score={validation_score}. "
        f"Lowest-signal sample expected {expected!r} but predicted {predicted!r}."
    )


def _optimization_dspy_context() -> Any:
    """Return a ``dspy.context`` scoping the planner LM for this run.

    Never mutates global ``dspy.settings``: if an outer caller already
    configured an LM the context is a passthrough; otherwise the planner LM is
    resolved from the environment and applied as a request-scoped override.
    """
    import dspy

    from fleet_rlm.runtime.config import build_dspy_context, resolve_lm

    if getattr(dspy.settings, "lm", None) is not None:
        return build_dspy_context()
    planner_lm = resolve_lm("planner")
    if planner_lm is None:
        raise RuntimeError(
            "DSPy LM is not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY "
            "in the environment before running offline optimization."
        )
    return build_dspy_context(lm=planner_lm)


def _build_optimizer(
    optimizer: OptimizerName,
    *,
    metric: Any,
    auto: Literal["light", "medium", "heavy"] | None,
    max_metric_calls: int | None = None,
    instruction_proposer: Any | None = None,
    reflection_lm_config: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
) -> tuple[Any, dict[str, str]]:
    """Instantiate GEPA and its reflection provenance."""
    if optimizer == "gepa":
        from dspy.teleprompt import GEPA

        reflection_lm = _resolve_reflection_lm(reflection_lm_config)
        auto_budget = None if max_metric_calls is not None else auto
        gepa_kwargs = _supported_kwargs(
            GEPA,
            {
                "metric": metric,
                "auto": auto_budget,
                "max_metric_calls": max_metric_calls,
                "reflection_lm": reflection_lm,
                "instruction_proposer": instruction_proposer,
                "log_dir": str(log_dir) if log_dir is not None else None,
                "track_stats": True,
                "track_best_outputs": True,
                "use_mlflow": False,
                "gepa_kwargs": {"use_cloudpickle": True},
            },
        )
        return (
            GEPA(**gepa_kwargs),
            _reflection_lm_provenance(reflection_lm, reflection_lm_config=reflection_lm_config),
        )
    raise ValueError(f"Unknown optimizer {optimizer!r}; expected one of {sorted(OPTIMIZER_LABELS)}.")


def run_module_optimization(
    spec: ModuleOptimizationSpec,
    *,
    dataset_path: str | Path,
    output_path: str | Path | None = None,
    default_output_root: str | Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
    optimizer: OptimizerName = "gepa",
    run_id: int | None = None,
    reflection_lm_config: dict[str, Any] | None = None,
    trace_bundle_paths: list[str] | None = None,
    max_metric_calls: int | None = None,
) -> OptimizationResult:
    """Run the full offline optimization pipeline for a module spec.

    Steps:
        1. Load and validate the dataset
        2. Convert rows to DSPy examples via the spec's row converter
        3. Split into train/val
        4. Build the spec's metric
        5. Compile with GEPA inside a scoped ``dspy.context`` (capture
           before/after prompt snapshots)
        6. ``dspy.Evaluate`` over the validation set (baseline + optimized)
        7. Save the optimized artifact
        8. Write manifest
        9. Persist evaluation results and prompt snapshots (when run_id given)
        10. Return a structured summary

    When *run_id* is provided, per-example evaluation results and before/after
    prompt snapshots are persisted to the local store.

    This function is safe to call from CLI or background tasks.
    It does **not** depend on MLflow or any live-request infrastructure.

    Raises:
        RuntimeError: If no DSPy LM is configured (for GEPA reflection or
            module execution).
        ValueError: If the dataset is empty or all rows are malformed.
    """
    optimizer_label = OPTIMIZER_LABELS.get(optimizer)
    if optimizer_label is None:
        raise ValueError(f"Unknown optimizer {optimizer!r}; expected one of {sorted(OPTIMIZER_LABELS)}.")

    dataset_path = Path(dataset_path)
    resolved_path = resolve_artifact_path(
        spec.module_slug,
        spec.artifact_filename,
        output_path,
        default_root=default_output_root,
    )
    manifest_path = resolved_path.with_suffix(".manifest.json")
    gepa_log_dir = resolved_path.with_suffix(".gepa")
    gepa_evidence_path = resolved_path.with_suffix(".gepa-evidence.json")
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Load + validate
    rows = load_dataset_rows(dataset_path)
    valid_rows = validate_required_keys(rows, spec.required_dataset_keys, spec.label)

    # 2. Convert
    examples = spec.row_converter(valid_rows)

    # 3. Split
    split = split_examples_with_metadata(examples, train_ratio=train_ratio)
    trainset, valset = split.train, split.validation

    # 4. Build metric
    metric = spec.metric_builder()

    # 5-7. Compile + evaluate inside a request-scoped LM context (no global
    # dspy.configure — see _optimization_dspy_context).
    with _optimization_dspy_context():
        program = spec.module_factory()
        before_snapshots = _capture_prompt_snapshots(program, "before")
        validation_dataset_indexes = split.validation_indexes
        has_val = len(valset) >= _MIN_VAL_EXAMPLES
        baseline_results = _evaluate_validation_set(program, valset, metric) if has_val else []
        baseline_validation_score = _mean_score(baseline_results)

        instruction_proposer = spec.instruction_proposer_factory() if spec.instruction_proposer_factory else None
        teleprompter, reflection_provenance = _build_optimizer(
            optimizer,
            metric=metric,
            auto=auto,
            max_metric_calls=max_metric_calls,
            instruction_proposer=instruction_proposer,
            reflection_lm_config=reflection_lm_config,
            log_dir=gepa_log_dir,
        )
        optimized = teleprompter.compile(
            program,
            trainset=trainset,
            valset=valset if has_val else None,
        )

        after_snapshots = _capture_prompt_snapshots(optimized, "after")

        # dspy.Evaluate over the validation set (when one exists)
        validation_score: float | None = None
        per_example_results: list[dict[str, Any]] = []
        if has_val:
            per_example_results = _evaluate_validation_set(optimized, valset, metric)
            validation_score = _mean_score(per_example_results)
        else:
            logger.warning(
                "Validation split is empty for %s — skipping evaluation. "
                "Provide more examples or a lower --train-ratio for validation scoring.",
                spec.module_slug,
            )

    # 8. Save artifact
    writer_metadata: dict[str, Any] = {}
    if spec.artifact_writer is not None:
        writer_metadata = dict(spec.artifact_writer(optimized, str(resolved_path)) or {})
    else:
        optimized.save(str(resolved_path))

    # 9. Write manifest
    gepa_evidence, gepa_candidate_decisions = _write_gepa_evidence_artifact(
        optimized=optimized,
        evidence_path=gepa_evidence_path,
    )
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
    artifact_metadata.update(writer_metadata)
    feedback_summary = _build_feedback_summary(per_example_results, validation_score)
    insights = _build_manifest_insights(
        prompt_snapshot_pairs=prompt_snapshot_pairs,
        trace_bundle_paths=list(trace_bundle_paths or []),
        validation_score=validation_score,
        baseline_validation_score=baseline_validation_score,
        candidate_decisions=gepa_candidate_decisions,
        has_external_validation=has_val,
    )
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
            "external_validation_available": has_val,
            "gepa_internal_valset": "validation" if has_val else "trainset_fallback",
            "promotion_ready": bool(has_val and validation_score is not None),
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
        "trace_bundle_paths": list(trace_bundle_paths or []),
        "gepa_evidence": {
            "available": gepa_evidence is not None,
            "path": str(gepa_evidence_path) if gepa_evidence is not None else None,
            "log_dir": str(gepa_log_dir),
            "candidate_count": (gepa_evidence or {}).get("candidate_count"),
            "best_candidate_id": (gepa_evidence or {}).get("best_candidate_id"),
            "total_metric_calls": (gepa_evidence or {}).get("total_metric_calls"),
            "num_full_val_evals": (gepa_evidence or {}).get("num_full_val_evals"),
        },
        "feedback_summary": feedback_summary,
        "insights": insights,
    }
    run_metadata = {
        "module_slug": spec.module_slug,
        "dataset_path": str(dataset_path),
        "reflection_profile_id": reflection_provenance.get("profile_id"),
        "reflection_model_id": reflection_provenance.get("model"),
        "max_metric_calls": max_metric_calls,
        "trace_bundle_paths": list(trace_bundle_paths or []),
        "distilled_trace_bundle_path": (trace_bundle_paths or [None])[0],
        "review_bundle": review_bundle,
    }
    manifest_data = build_manifest(
        module_spec=spec.program_spec,
        dataset_path=dataset_path,
        train_count=len(trainset),
        val_count=len(valset),
        validation_score=validation_score,
        optimizer=optimizer_label,
        metric_name=spec.metric_name or None,
        auto=auto,
        max_metric_calls=max_metric_calls,
        extra_metadata={
            "module_slug": spec.module_slug,
            "output_path": str(resolved_path),
            "artifact": artifact_metadata,
            "review_bundle": review_bundle,
            "insights": insights,
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
        optimizer=optimizer_label,
        program_spec=spec.program_spec,
        module_slug=spec.module_slug,
        evaluation_results=per_example_results,
        prompt_snapshots=before_snapshots + after_snapshots,
        review_bundle=review_bundle,
        run_metadata=run_metadata,
        feedback_summary=feedback_summary,
    )
