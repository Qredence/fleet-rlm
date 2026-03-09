"""Offline MLflow trace export and evaluation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import MlflowConfig
from .mlflow_integration import (
    get_mlflow_config,
    initialize_mlflow,
    search_annotated_trace_rows,
)


def export_annotated_trace_rows(
    *,
    output_path: Path,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    """Export annotated MLflow traces to a JSON dataset file."""
    rows = search_annotated_trace_rows(config=config, max_results=max_results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return rows


def load_trace_rows(path: Path) -> list[dict[str, Any]]:
    """Load exported trace rows from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON list of trace rows.")
    return [row for row in data if isinstance(row, dict)]


def rows_with_expected_responses(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter rows to those with an expected response for correctness scoring."""
    filtered: list[dict[str, Any]] = []
    for row in rows:
        expectations = row.get("expectations")
        if not isinstance(expectations, dict):
            continue
        expected_response = expectations.get("expected_response")
        if expected_response in (None, ""):
            continue
        filtered.append(row)
    return filtered


def build_default_scorers(
    *,
    include_safety: bool = False,
    guidelines: list[str] | None = None,
) -> list[Any]:
    """Build the default correctness-first MLflow GenAI scorers."""
    from mlflow.genai.scorers import Correctness, Guidelines, Safety

    scorers: list[Any] = [Correctness()]
    for index, guideline in enumerate(guidelines or [], start=1):
        scorers.append(
            Guidelines(
                name=f"guideline_{index}",
                guidelines=guideline,
            )
        )
    if include_safety:
        scorers.append(Safety())
    return scorers


def evaluate_trace_rows(
    rows: list[dict[str, Any]],
    *,
    config: MlflowConfig | None = None,
    include_safety: bool = False,
    guidelines: list[str] | None = None,
) -> Any:
    """Run MLflow GenAI evaluation against exported or live trace rows."""
    import mlflow

    resolved = config or get_mlflow_config()
    if not initialize_mlflow(resolved):
        raise RuntimeError("MLflow evaluation is unavailable. Check MLFLOW_* settings.")

    dataset = rows_with_expected_responses(rows)
    if not dataset:
        raise ValueError(
            "No annotated traces with expectations.expected_response were found."
        )

    scorers = build_default_scorers(
        include_safety=include_safety,
        guidelines=guidelines,
    )
    return mlflow.genai.evaluate(
        data=dataset,
        scorers=scorers,
        model_id=resolved.active_model_id,
    )


def serialize_evaluation_result(result: Any) -> dict[str, Any]:
    """Serialize an MLflow evaluation result into JSON-safe data."""
    metrics = dict(getattr(result, "metrics", {}) or {})
    raw_tables = dict(getattr(result, "tables", {}) or {})
    tables: dict[str, Any] = {}
    for name, table in raw_tables.items():
        if hasattr(table, "to_dict"):
            try:
                tables[name] = table.to_dict(orient="records")
                continue
            except TypeError:
                tables[name] = table.to_dict()
                continue
        tables[name] = str(table)
    return {
        "metrics": metrics,
        "tables": tables,
    }


def save_evaluation_result(*, result: Any, output_path: Path) -> dict[str, Any]:
    """Persist evaluation metrics/tables to JSON for contributor workflows."""
    payload = serialize_evaluation_result(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
