from __future__ import annotations

import pytest

from fleet_rlm.quality.dataset_versions import (
    DatasetVersionError,
    canonical_dataset_sha256,
    validate_approval_partitions,
    validate_dataset_partitions,
)
from fleet_rlm.quality.evaluation_protocol import DatasetPartition


def test_canonical_dataset_sha256_ignores_object_key_order() -> None:
    left = [{"answer": "a", "question": "q", "partition": "training"}]
    right = [{"partition": "training", "question": "q", "answer": "a"}]

    assert canonical_dataset_sha256(left) == canonical_dataset_sha256(right)


def test_canonical_dataset_sha256_detects_row_order_and_partition_changes() -> None:
    rows = [
        {"id": "one", "partition": "training"},
        {"id": "two", "partition": "selection"},
    ]

    assert canonical_dataset_sha256(rows) != canonical_dataset_sha256(list(reversed(rows)))
    assert canonical_dataset_sha256(rows) != canonical_dataset_sha256(
        [rows[0], {"id": "two", "partition": "promotion_test"}]
    )


def test_draft_partition_validation_allows_unassigned_but_rejects_unknown_values() -> None:
    validate_dataset_partitions([{"partition": "unassigned"}, {"partition": "training"}])

    with pytest.raises(DatasetVersionError, match="Unsupported dataset partition"):
        validate_dataset_partitions([{"partition": "mystery"}])


def test_metadata_partition_is_normalized_consistently() -> None:
    assert validate_dataset_partitions([{"metadata": {"partition": "selection"}}]) == (DatasetPartition.SELECTION,)


def test_approval_requires_all_rows_assigned_and_all_three_partitions() -> None:
    with pytest.raises(DatasetVersionError, match="unassigned"):
        validate_approval_partitions(["training", "selection", "promotion_test", "unassigned"])

    with pytest.raises(DatasetVersionError, match="training, selection, and promotion_test"):
        validate_approval_partitions(["training", "selection"])

    validate_approval_partitions(["training", "selection", "promotion_test"])
