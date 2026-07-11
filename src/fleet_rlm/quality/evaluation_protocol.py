"""Explicit dataset partitions for GEPA search and sealed promotion tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class DatasetPartition(StrEnum):
    TRAINING = "training"
    SELECTION = "selection"
    PROMOTION_TEST = "promotion_test"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True)
class PartitionedRows:
    training: tuple[dict[str, Any], ...]
    selection: tuple[dict[str, Any], ...]
    promotion_test: tuple[dict[str, Any], ...]
    unassigned: tuple[dict[str, Any], ...] = ()

    def partition_for(self, row_id: str) -> DatasetPartition | None:
        for partition, rows in (
            (DatasetPartition.TRAINING, self.training),
            (DatasetPartition.SELECTION, self.selection),
            (DatasetPartition.PROMOTION_TEST, self.promotion_test),
            (DatasetPartition.UNASSIGNED, self.unassigned),
        ):
            if any(str(row.get("id")) == row_id for row in rows):
                return partition
        return None


def _partition_value(row: dict[str, Any]) -> DatasetPartition:
    raw = row.get("partition")
    if raw is None and isinstance(row.get("metadata"), dict):
        raw = row["metadata"].get("partition")
    try:
        return DatasetPartition(str(raw or DatasetPartition.UNASSIGNED))
    except ValueError as exc:
        raise ValueError(f"Unsupported dataset partition: {raw!r}") from exc


def partition_rows(
    rows: Iterable[dict[str, Any]],
    *,
    require_promotion_test: bool = False,
) -> PartitionedRows:
    grouped: dict[DatasetPartition, list[dict[str, Any]]] = {partition: [] for partition in DatasetPartition}
    for row in rows:
        grouped[_partition_value(row)].append(row)

    if not grouped[DatasetPartition.TRAINING]:
        raise ValueError("Dataset Version must contain at least one training example.")
    if not grouped[DatasetPartition.SELECTION]:
        raise ValueError("Dataset Version must contain at least one selection example.")
    if require_promotion_test and not grouped[DatasetPartition.PROMOTION_TEST]:
        raise ValueError("Promotion-grade runs require at least one sealed promotion-test example.")

    return PartitionedRows(
        training=tuple(grouped[DatasetPartition.TRAINING]),
        selection=tuple(grouped[DatasetPartition.SELECTION]),
        promotion_test=tuple(grouped[DatasetPartition.PROMOTION_TEST]),
        unassigned=tuple(grouped[DatasetPartition.UNASSIGNED]),
    )


__all__ = ["DatasetPartition", "PartitionedRows", "partition_rows"]
