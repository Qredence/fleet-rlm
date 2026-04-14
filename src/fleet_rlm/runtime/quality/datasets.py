"""Shared dataset loading, validation, and split helpers for offline GEPA optimization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DatasetRow = dict[str, Any]


def load_dataset_rows(dataset_path: str | Path) -> list[DatasetRow]:
    """Load a JSON array or JSONL file of representative trace rows.

    Raises:
        FileNotFoundError: When *dataset_path* does not exist.
        ValueError: When the file is empty or has an unsupported format.
        json.JSONDecodeError: When the file contains malformed JSON.
    """
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Dataset is empty: {path}")

    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not rows:
            raise ValueError(f"JSONL dataset contains no parseable lines: {path}")
        return rows

    payload = json.loads(text)
    if isinstance(payload, list):
        if not payload:
            raise ValueError(f"JSON dataset is an empty array: {path}")
        return payload

    raise ValueError(
        f"Expected a JSON array or JSONL file of trace examples, got {type(payload).__name__}: {path}"
    )


def validate_required_keys(
    rows: list[DatasetRow],
    required_keys: list[str] | tuple[str, ...],
    module_name: str,
) -> list[DatasetRow]:
    """Filter rows to those containing all *required_keys*.

    Logs a warning for each skipped row and raises ``ValueError`` if no rows
    survive filtering.
    """
    valid: list[DatasetRow] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            logger.warning(
                "%s dataset row %d: expected dict, got %s — skipping",
                module_name,
                i,
                type(row).__name__,
            )
            continue
        missing = [k for k in required_keys if k not in row]
        if missing:
            logger.warning(
                "%s dataset row %d: missing keys %s — skipping",
                module_name,
                i,
                missing,
            )
            continue
        valid.append(row)

    if not valid:
        raise ValueError(
            f"No valid {module_name} examples after filtering {len(rows)} rows "
            f"for required keys {list(required_keys)}."
        )

    skipped = len(rows) - len(valid)
    if skipped:
        logger.info(
            "%s dataset: %d of %d rows passed validation (%d skipped)",
            module_name,
            len(valid),
            len(rows),
            skipped,
        )
    return valid


def split_examples(
    examples: list[Any],
    *,
    train_ratio: float = 0.8,
) -> tuple[list[Any], list[Any]]:
    """Split examples into train/validation partitions.

    This is the canonical split helper for offline GEPA optimization.
    Re-exported here from ``mlflow_optimization.split_examples`` for
    consistency; both locations remain importable for backward compatibility.
    """
    if not examples:
        raise ValueError("No optimization examples were produced from the dataset.")
    if len(examples) == 1:
        return examples, []
    cutoff = max(1, min(len(examples) - 1, int(len(examples) * train_ratio)))
    return examples[:cutoff], examples[cutoff:]
