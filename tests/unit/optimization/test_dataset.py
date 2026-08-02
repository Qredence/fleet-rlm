"""Unit contracts for curated, sealed optimization datasets."""

from __future__ import annotations

import pytest

from fleet_rlm.optimization.dataset import (
    EXPORT_SCHEMA,
    OptimizationDatasetError,
    load_export,
    split_records,
    validate_records,
)


def _record(index: int) -> dict:
    return {
        "record_id": f"record-{index:03d}",
        "task": {"query": f"safe question {index}"},
        "output_contract": {"schema": "answer-v1"},
        "expectations": {"expected_response": f"answer {index}", "grounding": ["fixture"]},
        "execution_requirements": {"typed_submit": True},
        "provenance": {"redaction_version": "v1", "source": "synthetic"},
    }


def test_split_is_order_independent_and_has_60_20_20_partitions() -> None:
    records = validate_records([_record(index) for index in range(25)])
    normal = split_records(records, seed=7)
    reversed_split = split_records(list(reversed(records)), seed=7)

    assert [record.record_id for record in normal.train] == [record.record_id for record in reversed_split.train]
    assert len(normal.train) == 15
    assert len(normal.selection) == 5
    assert len(normal.sealed_test) == 5
    assert "sealed_test" in normal.public_manifest
    assert "record_id" not in str(normal.public_manifest["sealed_test"])


def test_records_reject_small_duplicate_and_raw_runtime_exports() -> None:
    with pytest.raises(OptimizationDatasetError, match="at least 25"):
        validate_records([_record(index) for index in range(24)])

    duplicate = [_record(index) for index in range(24)] + [_record(0)]
    with pytest.raises(OptimizationDatasetError, match="duplicate"):
        validate_records(duplicate)

    unsafe = [_record(index) for index in range(25)]
    unsafe[0]["task"]["query"] = "look at .fleet_rlm/local.sqlite3"
    with pytest.raises(OptimizationDatasetError, match="raw-state"):
        validate_records(unsafe)


def test_load_export_requires_versioned_container() -> None:
    payload = {"schema": EXPORT_SCHEMA, "records": [_record(index) for index in range(25)]}
    assert len(load_export(payload)) == 25
    payload["schema"] = "wrong"
    with pytest.raises(OptimizationDatasetError, match="schema"):
        load_export(payload)
