"""Unit contracts for curated inputs, datasets, and metrics.

Consolidates:
- Curated evaluation input store and handle projection
- Dataset validation, export loading, and leakage-preventing partitioning
- Trusted metric evaluation, feedback scoring, and policy hashing
"""

from __future__ import annotations

import json

import dspy
import pytest

from fleet_rlm.optimization.curated_input import CuratedEvaluationStore, CuratedInputError
from fleet_rlm.optimization.dataset import (
    EXPORT_SCHEMA,
    OptimizationDatasetError,
    load_export,
    split_records,
    validate_records,
)
from fleet_rlm.optimization.metric import (
    ScoreFeedback,
    TrustedGEPAFeedbackMetric,
    TrustedMetricError,
    expectation_score,
    scorer_policy_sha256,
)
from fleet_rlm.optimization.types import OptimizationRecord

# ==============================================================================
# Curated Evaluation Input Contracts
# ==============================================================================


def _curated_record() -> OptimizationRecord:
    return OptimizationRecord(
        record_id="record-1",
        query="summarize the synthetic report",
        output_contract={"answer": "string"},
        expectations={"must_include": ["A"]},
        execution_requirements={"no_network": True},
        provenance={"redaction_version": "v1"},
        content_sha256="a" * 64,
    )


def test_store_uses_stable_canonical_digest_and_handle_metadata() -> None:
    first = CuratedEvaluationStore(candidate="candidate", record=_curated_record())
    second = CuratedEvaluationStore(candidate="candidate", record=_curated_record())

    assert first.receipt.sha256 == second.receipt.sha256
    assert first.handle.sha256 == first.receipt.sha256
    assert first.handle.schema == "fleet.curated-evaluation-input/v1"
    assert first.handle.byte_size > 0
    assert first.handle.transaction_id != second.handle.transaction_id


def test_read_returns_detached_bounded_projection() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_curated_record())
    handle = store.handle

    response = store.read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/record/expectations",
    )
    value = json.loads(response["json"])
    value["must_include"].append("forged")

    reread = store.read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/record/expectations",
    )
    assert json.loads(reread["json"]) == {"must_include": ["A"]}
    assert response["complete"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"transaction_id": "wrong", "sha256": "a" * 64}, "unknown"),
        ({"json_pointer": "/record/provenance"}, "not permitted"),
        ({"start": -1}, "negative"),
        ({"limit": 0}, "maximum"),
        ({"limit": 8_001}, "maximum"),
    ],
)
def test_read_rejects_invalid_capability_or_bounds(kwargs: dict[str, object], message: str) -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_curated_record())
    handle = store.handle
    values: dict[str, object] = {
        "transaction_id": handle.transaction_id,
        "sha256": handle.sha256,
    }
    values.update(kwargs)

    with pytest.raises(CuratedInputError, match=message):
        store.read(**values)  # type: ignore[arg-type]


def test_broker_tool_still_reads_host_canonical_input_after_handle_rebinding() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_curated_record())
    handle = store.handle
    read = store.broker_tool(handle=handle)

    response = read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/candidate",
    )
    assert json.loads(response["json"]) == "candidate"


def test_store_is_single_use_for_host_lifecycle_accounting() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_curated_record())

    receipt = store.consume()
    assert receipt.sha256 == store.handle.sha256
    with pytest.raises(CuratedInputError, match="already consumed"):
        store.consume()


# ==============================================================================
# Dataset Validation & Split Contracts
# ==============================================================================


def _dataset_record(index: int) -> dict:
    return {
        "record_id": f"record-{index:03d}",
        "task": {"query": f"safe question {index}"},
        "output_contract": {"schema": "answer-v1"},
        "expectations": {"expected_response": f"answer {index}", "grounding": ["fixture"]},
        "execution_requirements": {"typed_submit": True},
        "provenance": {"redaction_version": "v1", "source": "synthetic"},
    }


def test_split_is_order_independent_and_has_60_20_20_partitions() -> None:
    records = validate_records([_dataset_record(index) for index in range(25)])
    normal = split_records(records, seed=7)
    reversed_split = split_records(list(reversed(records)), seed=7)

    assert [record.record_id for record in normal.train] == [record.record_id for record in reversed_split.train]
    assert len(normal.train) == 15
    assert len(normal.selection) == 5
    assert len(normal.sealed_test) == 5
    assert "sealed_test" in normal.public_manifest
    assert "record_id" not in str(normal.public_manifest["sealed_test"])


def test_load_export_requires_versioned_container() -> None:
    payload = {"schema": EXPORT_SCHEMA, "records": [_dataset_record(index) for index in range(25)]}
    assert len(load_export(payload)) == 25
    payload["schema"] = "wrong"
    with pytest.raises(OptimizationDatasetError, match="schema"):
        load_export(payload)


def test_single_project_cannot_leak_into_held_out_split() -> None:
    raw = [_dataset_record(index) for index in range(25)]
    for record in raw:
        record["provenance"]["project_id"] = "one-project"
    with pytest.raises(OptimizationDatasetError, match="isolated partitions"):
        split_records(validate_records(raw), seed=7)


def test_task_families_cannot_leak_across_different_sessions_and_projects() -> None:
    raw = [_dataset_record(index) for index in range(30)]
    for index, record in enumerate(raw):
        record["provenance"].update(
            session_id=f"session-{index}", project_id=f"project-{index}", task_family=f"family-{index // 5}"
        )
    split = split_records(validate_records(raw), seed=42)
    assert split.grouping == "session-project-family"
    assignments: dict[str, int] = {}
    for partition, group in enumerate((split.train, split.selection, split.sealed_test)):
        for record in group:
            assert assignments.setdefault(record.provenance["task_family"], partition) == partition


def test_dataset_manifest_digest_changes_when_expected_answer_changes() -> None:
    raw = [_dataset_record(index) for index in range(25)]
    before = split_records(validate_records(raw), seed=7).public_manifest["dataset_sha256"]
    raw[0]["expectations"]["expected_response"] = "corrected answer"
    after = split_records(validate_records(raw), seed=7).public_manifest["dataset_sha256"]
    assert before != after


@pytest.mark.parametrize("identity", [None, "", {}, "private/project"])
def test_group_identity_requires_an_explicit_opaque_identifier(identity: object) -> None:
    raw = [_dataset_record(index) for index in range(25)]
    raw[0]["provenance"]["session_id"] = identity
    with pytest.raises(OptimizationDatasetError, match="opaque safe identifier"):
        validate_records(raw)


# ==============================================================================
# Metric Scoring & Feedback Contracts
# ==============================================================================


def _gold(expectations: dict) -> dict:
    return {"expectations": expectations}


def test_expectation_metric_returns_exact_prediction_feedback() -> None:
    result = expectation_score(_gold({"expected_response": "703"}), {"answer": "703"})
    assert result == ScoreFeedback(1.0, "all machine-checkable expectations passed")

    metric = TrustedGEPAFeedbackMetric(lambda _gold, _pred, **_kwargs: result, "a" * 64)
    prediction = metric(_gold({"expected_response": "703"}), {"answer": "703"})
    assert isinstance(prediction, dspy.Prediction)
    assert prediction.score == 1.0
    assert prediction.feedback == "all machine-checkable expectations passed"


def test_qualitative_only_expectations_are_not_silently_scored_as_pass() -> None:
    result = expectation_score(_gold({"criteria": ["Explain the evidence."]}), {"answer": "anything"})
    assert result.score == 0.0
    assert "unscorable" in result.feedback


@pytest.mark.parametrize(
    "feedback",
    ["", "api_key leaked", "token=secret", "x" * 2001],
)
def test_score_feedback_rejects_sensitive_or_unbounded_feedback(feedback: str) -> None:
    with pytest.raises(TrustedMetricError):
        ScoreFeedback(0.5, feedback)


def test_metric_failure_is_bounded_and_does_not_expose_exception_text() -> None:
    metric = TrustedGEPAFeedbackMetric(
        lambda _gold, _pred, **_kwargs: (_ for _ in ()).throw(RuntimeError("password=do-not-expose")),
        "b" * 64,
    )
    result = metric({}, {})
    assert result.score == 0.0
    assert result.feedback == "trusted_scorer_failure: RuntimeError"
    assert "password" not in result.feedback


def test_metric_rejects_nonzero_failure_score() -> None:
    with pytest.raises(TrustedMetricError, match="failure score must be exactly zero"):
        TrustedGEPAFeedbackMetric(lambda _gold, _pred, **_kwargs: ScoreFeedback(1.0, "ok"), "b" * 64, 1.0)


def test_metric_requires_digest_and_policy_hash_is_stable() -> None:
    with pytest.raises(TrustedMetricError):
        TrustedGEPAFeedbackMetric(lambda _gold, _pred, **_kwargs: ScoreFeedback(1.0, "ok"), "short")
    assert scorer_policy_sha256({"model": "judge-v1", "temperature": 0}) == scorer_policy_sha256(
        {"temperature": 0, "model": "judge-v1"}
    )


# ==============================================================================
# Canonical Digest Pins
# ==============================================================================
# Values were computed before the shared canonical JSON digest helper existed.
# A change here means persisted handles, dataset manifests, and promotion
# policy identities would silently change.


def test_curated_input_digests_are_pinned_for_persisted_handles() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_curated_record())
    unicode_store = CuratedEvaluationStore(candidate="candidate café ✓", record=_curated_record())
    projection = store.read(
        transaction_id=store.handle.transaction_id,
        sha256=store.handle.sha256,
        json_pointer="/candidate",
    )

    assert store.receipt.sha256 == "93c0ab476deee78dab4f5b1d8c13f5cffeb5d8001c67dc825c183678176e4c95"
    assert unicode_store.receipt.sha256 == "89e50abb80ec7d20292db77be19d61e22967b78d0cff5a74d924b36f61fc6a2f"
    assert projection["sha256"] == "6201e275c993fded1b6a1b7b64f106686c702d9fdff8af32b560aad5d781015c"


def test_dataset_content_and_manifest_digests_are_pinned_for_leakage_audit() -> None:
    records = validate_records([_dataset_record(index) for index in range(25)])
    manifest = split_records(records, seed=7).public_manifest

    assert records[0].content_sha256 == "2d566a2fde69819067efab6f542fd12b64db239f3f06392cf9bb8613a3e52425"
    assert records[24].content_sha256 == "cc36c96f8bc1000c677f8895c3e4fd730aedfa023f3eae476a97b2b0e2ed740e"
    assert manifest["dataset_sha256"] == "85975c3ca011aa3c9e3b24af179be4154bd7f98b5a5ccc0601063612a61fd884"
    assert manifest["sealed_test"]["ids_sha256"] == "82a493b0678850a682e4686bc9df8f96638ed86015025b6d65af1583de00ea3d"


def test_scorer_policy_digests_are_pinned_for_promotion_identity() -> None:
    assert scorer_policy_sha256({"model": "judge-v1", "temperature": 0}) == (
        "995ad4f738bd78709c0e0d6139b8e69d20f2dbf1acd63fb69d485472ea44948e"
    )
    assert scorer_policy_sha256({"model": "judge-ü", "threshold": 0.5, "rubric": ["a", "b"]}) == (
        "8c4a310634f7770cf4ee7b14b3d8f164183cfe74f90498758620fef24be2229c"
    )
