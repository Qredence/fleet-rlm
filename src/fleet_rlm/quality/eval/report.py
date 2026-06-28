"""EvaluationReport dataclass with JSON serialization.

This module provides the EvaluationReport class for storing evaluation
results with per-trace scores and aggregate statistics.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .judges import JUDGE_NAMES
from .metrics import METRIC_NAMES


@dataclass
class EvaluationReport:
    """Container for evaluation results with per-trace scores and aggregates.

    Attributes:
        run_id: Unique identifier for this evaluation run.
        created_at: ISO8601 timestamp when the report was created.
        filters: Dictionary echoing the trace_ids/limit/from_last_days used.
        per_trace: List of per-trace score dictionaries.
        aggregates: Dictionary with mean and median for each score.
    """

    run_id: str
    created_at: str
    filters: dict[str, Any]
    per_trace: list[dict[str, Any]]
    aggregates: dict[str, dict[str, float]]

    @classmethod
    def build(
        cls,
        run_id: str,
        filters: dict[str, Any],
        per_trace: list[dict[str, Any]],
    ) -> EvaluationReport:
        """Build an EvaluationReport with computed aggregates.

        Args:
            run_id: Unique identifier for this evaluation run.
            filters: Dictionary echoing the filters used (trace_ids, limit, from_last_days).
            per_trace: List of per-trace score dictionaries.

        Returns:
            A new EvaluationReport with aggregates computed.
        """
        created_at = datetime.now(UTC).isoformat()

        # Compute aggregates
        aggregates = cls._compute_aggregates(per_trace)

        return cls(
            run_id=run_id,
            created_at=created_at,
            filters=filters,
            per_trace=per_trace,
            aggregates=aggregates,
        )

    @staticmethod
    def _compute_aggregates(per_trace: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
        """Compute mean and median for each score across all traces.

        Args:
            per_trace: List of per-trace score dictionaries.

        Returns:
            Dictionary with 'mean' and 'median' sub-dictionaries.
        """
        if not per_trace:
            # Return empty aggregates
            return {
                "mean": {name: 0.0 for name in JUDGE_NAMES + METRIC_NAMES},
                "median": {name: 0.0 for name in JUDGE_NAMES + METRIC_NAMES},
            }

        # Collect all scores for each metric
        all_scores: dict[str, list[float]] = {name: [] for name in JUDGE_NAMES + METRIC_NAMES}

        for trace_scores in per_trace:
            for name in JUDGE_NAMES + METRIC_NAMES:
                value = trace_scores.get(name)
                if value is not None and isinstance(value, (int, float)):
                    all_scores[name].append(float(value))

        # Compute mean and median
        aggregates: dict[str, dict[str, float]] = {
            "mean": {},
            "median": {},
        }

        for name in JUDGE_NAMES + METRIC_NAMES:
            scores = all_scores[name]
            if not scores:
                aggregates["mean"][name] = 0.0
                aggregates["median"][name] = 0.0
                continue

            # Mean
            mean_val = sum(scores) / len(scores)
            aggregates["mean"][name] = mean_val

            # Median
            sorted_scores = sorted(scores)
            n = len(sorted_scores)
            if n % 2 == 0:
                median_val = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2.0
            else:
                median_val = sorted_scores[n // 2]
            aggregates["median"][name] = median_val

        return aggregates

    def to_json(self) -> str:
        """Serialize the report to JSON.

        Returns:
            JSON string representation of the report.
        """
        return json.dumps(asdict(self), indent=2, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> EvaluationReport:
        """Deserialize a report from JSON.

        Args:
            json_str: JSON string representation of a report.

        Returns:
            A new EvaluationReport instance.

        Raises:
            json.JSONDecodeError: If the JSON is invalid.
            KeyError: If required fields are missing.
        """
        data = json.loads(json_str)
        return cls(
            run_id=str(data["run_id"]),
            created_at=str(data["created_at"]),
            filters=data.get("filters", {}),
            per_trace=data.get("per_trace", []),
            aggregates=data.get("aggregates", {}),
        )

    def write_to_disk(self, output_dir: Path | str) -> Path:
        """Write the report to disk as report.json.

        Args:
            output_dir: Directory to write the report to.

        Returns:
            Path to the written report.json file.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        report_file = output_path / "report.json"
        report_file.write_text(self.to_json(), encoding="utf-8")
        return report_file

    @classmethod
    def read_from_disk(cls, report_path: Path | str) -> EvaluationReport:
        """Read a report from disk.

        Args:
            report_path: Path to report.json or directory containing it.

        Returns:
            A new EvaluationReport instance.

        Raises:
            FileNotFoundError: If the report file doesn't exist.
        """
        path = Path(report_path)
        if path.is_dir():
            path = path / "report.json"
        return cls.from_json(path.read_text(encoding="utf-8"))
