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


def test_load_export_requires_versioned_container() -> None:
    payload = {"schema": EXPORT_SCHEMA, "records": [_record(index) for index in range(25)]}
    assert len(load_export(payload)) == 25
    payload["schema"] = "wrong"
    with pytest.raises(OptimizationDatasetError, match="schema"):
        load_export(payload)


def test_single_project_cannot_leak_into_held_out_split():
    raw = [_record(index) for index in range(25)]
    for record in raw:
        record["provenance"]["project_id"] = "one-project"
    with pytest.raises(OptimizationDatasetError, match="isolated partitions"):
        split_records(validate_records(raw), seed=7)


def test_task_families_cannot_leak_across_different_sessions_and_projects():
    raw = [_record(index) for index in range(30)]
    for index, record in enumerate(raw):
        record["provenance"].update(
            session_id=f"session-{index}", project_id=f"project-{index}", task_family=f"family-{index // 5}"
        )
    split = split_records(validate_records(raw), seed=42)
    assert split.grouping == "session-project-family"
    assignments = {}
    for partition, group in enumerate((split.train, split.selection, split.sealed_test)):
        for record in group:
            assert assignments.setdefault(record.provenance["task_family"], partition) == partition


def test_dataset_manifest_digest_changes_when_expected_answer_changes():
    raw = [_record(index) for index in range(25)]
    before = split_records(validate_records(raw), seed=7).public_manifest["dataset_sha256"]
    raw[0]["expectations"]["expected_response"] = "corrected answer"
    after = split_records(validate_records(raw), seed=7).public_manifest["dataset_sha256"]
    assert before != after


@pytest.mark.parametrize("identity", [None, "", {}, "private/project"])
def test_group_identity_requires_an_explicit_opaque_identifier(identity):
    raw = [_record(index) for index in range(25)]
    raw[0]["provenance"]["session_id"] = identity
    with pytest.raises(OptimizationDatasetError, match="opaque safe identifier"):
        validate_records(raw)
