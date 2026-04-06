"""Offline DSPy optimization helpers with MLflow autologging."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Literal, cast

import dspy
from dspy.teleprompt import MIPROv2

from fleet_rlm.integrations.observability.config import MlflowConfig
from .mlflow_evaluation import load_trace_rows, rows_with_expected_responses
from fleet_rlm.integrations.observability.mlflow_runtime import initialize_mlflow


def load_symbol(spec: str) -> Any:
    """Resolve a ``module:attr`` symbol reference."""
    if ":" not in spec:
        raise ValueError("Expected symbol in the form 'module:attr'.")
    module_name, attr_name = spec.split(":", 1)
    module = import_module(module_name)
    return getattr(module, attr_name)


def build_program(spec: str) -> dspy.Module:
    """Instantiate a DSPy program from a symbol reference."""
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


def _prediction_field(prediction: Any, output_key: str) -> str:
    if isinstance(prediction, dict):
        value = prediction.get(output_key)
    else:
        value = getattr(prediction, output_key, None)
    return str(value or "").strip()


def build_exact_match_metric(output_key: str) -> Callable[..., bool]:
    """Build a simple exact-match metric for DSPy optimization/evaluation."""

    def metric(example: Any, prediction: Any, trace: Any = None) -> bool:
        _ = trace
        expected = str(getattr(example, output_key, "") or "").strip()
        actual = _prediction_field(prediction, output_key)
        return bool(expected) and expected == actual

    return metric


def rows_to_examples(
    rows: list[dict[str, Any]],
    *,
    input_keys: list[str] | None = None,
    output_key: str = "answer",
) -> list[dspy.Example]:
    """Convert exported MLflow trace rows into DSPy examples."""
    examples: list[dspy.Example] = []
    for row in rows_with_expected_responses(rows):
        inputs = row.get("inputs")
        expectations = row.get("expectations")
        if not isinstance(inputs, dict) or not isinstance(expectations, dict):
            continue

        expected_response = expectations.get("expected_response")
        if expected_response in (None, ""):
            continue

        resolved_input_keys = input_keys or list(inputs.keys())
        if not resolved_input_keys:
            continue

        example = dspy.Example(
            **inputs,
            **{output_key: expected_response},
        ).with_inputs(*resolved_input_keys)
        examples.append(example)
    return examples


def split_examples(
    examples: list[dspy.Example],
    *,
    train_ratio: float = 0.8,
) -> tuple[list[dspy.Example], list[dspy.Example]]:
    """Split examples into train/validation partitions."""
    if not examples:
        raise ValueError("No optimization examples were produced from the dataset.")

    if len(examples) == 1:
        return examples, []

    cutoff = max(1, min(len(examples) - 1, int(len(examples) * train_ratio)))
    return examples[:cutoff], examples[cutoff:]


def optimize_program_with_mipro(
    *,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None = None,
    input_keys: list[str] | None = None,
    output_key: str = "answer",
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
    run_name: str | None = None,
    config: MlflowConfig | None = None,
) -> dict[str, Any]:
    """Run MIPROv2 against an exported MLflow dataset under MLflow autologging."""
    import mlflow

    resolved = (config or MlflowConfig.from_env()).model_copy(
        update={
            "dspy_log_traces_from_compile": True,
            "dspy_log_traces_from_eval": True,
            "dspy_log_compiles": True,
            "dspy_log_evals": True,
        }
    )
    if not initialize_mlflow(resolved):
        raise RuntimeError(
            "MLflow optimization is unavailable. Check MLFLOW_* settings."
        )

    rows = load_trace_rows(dataset_path)
    examples = rows_to_examples(
        rows,
        input_keys=input_keys,
        output_key=output_key,
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_exact_match_metric(output_key)
    program = build_program(program_spec)

    optimizer = MIPROv2(metric=metric, auto=auto)
    resolved_run_name = run_name or f"MIPROv2::{program_spec}"
    start_run = getattr(mlflow, "start_run", None)
    log_metric = getattr(mlflow, "log_metric", None)
    if start_run is None or log_metric is None:
        raise RuntimeError(
            "MLflow tracking helpers are unavailable in this environment."
        )

    with cast(Any, start_run)(run_name=resolved_run_name):
        optimized = optimizer.compile(
            program,
            trainset=trainset,
            valset=valset or None,
        )
        validation_score = None
        if valset:
            evaluator = dspy.Evaluate(devset=valset, metric=metric)
            validation_score = evaluator(optimized)
            cast(Any, log_metric)(
                "optimization_validation_score", float(validation_score)
            )

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            optimized.save(str(output_path))

    return {
        "train_examples": len(trainset),
        "validation_examples": len(valset),
        "output_path": str(output_path) if output_path is not None else None,
        "validation_score": validation_score,
        "program_spec": program_spec,
        "optimizer": "MIPROv2",
    }
