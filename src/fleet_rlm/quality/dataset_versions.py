"""Pure contracts for immutable managed Dataset Versions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from .evaluation_protocol import DatasetPartition


class DatasetVersionError(ValueError):
    """Raised when a managed Dataset Version violates its lifecycle contract."""


def canonical_dataset_sha256(rows: Sequence[dict[str, Any]]) -> str:
    """Hash ordered rows with stable object-key serialization."""
    normalized: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["partition"] = partition_value(source).value
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and "partition" in metadata:
            cleaned_metadata = dict(metadata)
            cleaned_metadata.pop("partition", None)
            if cleaned_metadata:
                row["metadata"] = cleaned_metadata
            else:
                row.pop("metadata", None)
        normalized.append(row)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def partition_value(row: dict[str, Any]) -> DatasetPartition:
    raw = row.get("partition")
    if raw is None and isinstance(row.get("metadata"), dict):
        raw = row["metadata"].get("partition")
    try:
        return DatasetPartition(str(raw or DatasetPartition.UNASSIGNED))
    except ValueError as exc:
        raise DatasetVersionError(f"Unsupported dataset partition: {raw!r}") from exc


def validate_dataset_partitions(rows: Iterable[dict[str, Any]]) -> tuple[DatasetPartition, ...]:
    """Validate draft partition labels while allowing unassigned rows."""
    return tuple(partition_value(row) for row in rows)


def validate_approval_partitions(partitions: Iterable[str | DatasetPartition]) -> None:
    """Require a fully assigned three-way dataset before approval."""
    try:
        normalized = tuple(DatasetPartition(str(partition)) for partition in partitions)
    except ValueError as exc:
        raise DatasetVersionError("Dataset contains an unsupported partition.") from exc
    if DatasetPartition.UNASSIGNED in normalized:
        raise DatasetVersionError("Approved Dataset Versions cannot contain unassigned rows.")
    required = {
        DatasetPartition.TRAINING,
        DatasetPartition.SELECTION,
        DatasetPartition.PROMOTION_TEST,
    }
    if not required.issubset(normalized):
        raise DatasetVersionError("Dataset must contain training, selection, and promotion_test partitions.")


__all__ = [
    "DatasetVersionError",
    "canonical_dataset_sha256",
    "partition_value",
    "validate_approval_partitions",
    "validate_dataset_partitions",
]
