"""Shared dataset loading, conversion, validation, and split helpers for offline optimization."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

import dspy

logger = logging.getLogger(__name__)

DatasetRow = dict[str, Any]


@dataclass(frozen=True)
class ExampleSplit:
    """Train/validation split with original dataset index metadata."""

    train: list[Any]
    validation: list[Any]
    train_indexes: list[int]
    validation_indexes: list[int]
    strategy: str
    stratify_by: list[str]
    strata: dict[str, dict[str, int]]


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

    raise ValueError(f"Expected a JSON array or JSONL file of trace examples, got {type(payload).__name__}: {path}")


def rows_to_examples(
    rows: list[DatasetRow],
    *,
    input_keys: list[str] | None = None,
    output_key: str = "response",
) -> list[dspy.Example]:
    """Convert exported MLflow trace rows into DSPy examples.

    Rows must carry an ``inputs`` dict and an ``expectations`` dict with a
    non-empty ``expected_response``; other rows are skipped.
    """
    examples: list[dspy.Example] = []
    for row in rows:
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


def validate_required_keys(
    rows: Sequence[object],
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
        valid.append(cast(DatasetRow, row))

    if not valid:
        raise ValueError(
            f"No valid {module_name} examples after filtering {len(rows)} rows for required keys {list(required_keys)}."
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

    This is the canonical split helper for offline optimization.
    """
    if not examples:
        raise ValueError("No optimization examples were produced from the dataset.")
    if len(examples) == 1:
        return examples, []
    cutoff = max(1, min(len(examples) - 1, int(len(examples) * train_ratio)))
    return examples[:cutoff], examples[cutoff:]


def _prefix_split_indexes(total_examples: int, train_ratio: float) -> tuple[list[int], list[int]]:
    """Return deterministic prefix train/validation indexes."""
    if total_examples <= 0:
        raise ValueError("No optimization examples were produced from the dataset.")
    if total_examples == 1:
        return [0], []
    cutoff = max(1, min(total_examples - 1, int(total_examples * train_ratio)))
    return list(range(cutoff)), list(range(cutoff, total_examples))


def _example_metadata_value(example: Any, key: str) -> str | None:
    """Return a normalized metadata value from a DSPy example-like object."""
    value: Any = None
    if isinstance(example, dict):
        value = example.get(key)
    else:
        value = getattr(example, key, None)
        if value is None and hasattr(example, "get"):
            try:
                value = example.get(key)
            except Exception:
                value = None
    normalized = str(value or "").strip()
    return normalized or None


def _stratum_key(example: Any, stratify_by: Sequence[str]) -> str | None:
    """Return a stable stratum key when all requested metadata values exist."""
    parts: list[str] = []
    for key in stratify_by:
        value = _example_metadata_value(example, key)
        if value is None:
            return None
        parts.append(f"{key}={value}")
    return "|".join(parts)


def _contiguous_indexes(indexes: list[int]) -> bool:
    """Return True when indexes form one contiguous increasing range."""
    return not indexes or indexes == list(range(indexes[0], indexes[-1] + 1))


def split_examples_with_metadata(
    examples: list[Any],
    *,
    train_ratio: float = 0.8,
    stratify_by: Sequence[str] = ("domain", "difficulty"),
) -> ExampleSplit:
    """Split examples while preserving original dataset indexes.

    When every example has the requested metadata and more than one stratum is
    present, validation examples are selected from the tail of each stratum.
    Otherwise the helper falls back to the prefix split used by
    ``split_examples``.
    """
    if not examples:
        raise ValueError("No optimization examples were produced from the dataset.")

    train_indexes, validation_indexes = _prefix_split_indexes(len(examples), train_ratio)
    if len(examples) == 1:
        return ExampleSplit(
            train=[examples[0]],
            validation=[],
            train_indexes=train_indexes,
            validation_indexes=validation_indexes,
            strategy="single-example",
            stratify_by=[],
            strata={},
        )

    normalized_keys = [key for key in stratify_by if key]
    groups: dict[str, list[int]] = {}
    if normalized_keys:
        for index, example in enumerate(examples):
            key = _stratum_key(example, normalized_keys)
            if key is None:
                groups = {}
                break
            groups.setdefault(key, []).append(index)

    if len(groups) <= 1:
        return ExampleSplit(
            train=[examples[index] for index in train_indexes],
            validation=[examples[index] for index in validation_indexes],
            train_indexes=train_indexes,
            validation_indexes=validation_indexes,
            strategy="prefix",
            stratify_by=[],
            strata={},
        )

    train_index_set: set[int] = set()
    validation_index_set: set[int] = set()
    strata: dict[str, dict[str, int]] = {}
    for key, indexes in groups.items():
        if len(indexes) == 1:
            train_index_set.update(indexes)
            strata[key] = {"total": 1, "train": 1, "validation": 0}
            continue

        validation_count = max(1, round(len(indexes) * (1 - train_ratio)))
        validation_count = min(validation_count, len(indexes) - 1)
        stratum_validation = indexes[-validation_count:]
        stratum_train = indexes[:-validation_count]
        train_index_set.update(stratum_train)
        validation_index_set.update(stratum_validation)
        strata[key] = {
            "total": len(indexes),
            "train": len(stratum_train),
            "validation": len(stratum_validation),
        }

    if not validation_index_set:
        return ExampleSplit(
            train=[examples[index] for index in train_indexes],
            validation=[examples[index] for index in validation_indexes],
            train_indexes=train_indexes,
            validation_indexes=validation_indexes,
            strategy="prefix",
            stratify_by=[],
            strata={},
        )

    train_indexes = sorted(train_index_set)
    validation_indexes = sorted(validation_index_set)
    return ExampleSplit(
        train=[examples[index] for index in train_indexes],
        validation=[examples[index] for index in validation_indexes],
        train_indexes=train_indexes,
        validation_indexes=validation_indexes,
        strategy="stratified-metadata",
        stratify_by=normalized_keys,
        strata=strata,
    )


def validation_range_for_indexes(indexes: list[int]) -> dict[str, int | None]:
    """Return a compact validation range only when indexes are contiguous."""
    if not indexes or not _contiguous_indexes(indexes):
        return {"start": None, "end_exclusive": None}
    return {"start": indexes[0], "end_exclusive": indexes[-1] + 1}
