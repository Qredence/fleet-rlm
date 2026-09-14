"""Curation never derives ground truth from model output or executes trace text."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.benchmarks import curate_mlflow as curation


def _trace(index=0):
    return SimpleNamespace(
        info=SimpleNamespace(trace_id=f"tr-{index}", trace_metadata={"mlflow.trace.session": f"session-{index}"}),
        data=SimpleNamespace(
            spans=[SimpleNamespace(parent_id=None, name="fleet_turn", inputs={"request": f"Compute {index} plus 1."})]
        ),
    )


def _inputs():
    snapshot = {"schema": curation.SNAPSHOT_SCHEMA, "records": [curation.capture_record(_trace(i)) for i in range(25)]}
    snapshot["snapshot_sha256"] = curation.digest(snapshot)
    review = {
        "schema": "fleet.phase6-curation-review/v1",
        "reviewer": "agent",
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "records": [
            {
                "record_id": source["record_id"],
                "source_query_sha256": source["source_query_sha256"],
                "review_status": "agent_reviewed",
                "self_contained": True,
                "task_family": f"family-{i // 5}",
                "task_origin": "synthetic-unit-test",
                "expectations": {"criteria": [f"Answer is {i + 1}."]},
                "output_contract": {"schema": "answer-v1"},
                "execution_requirements": {"typed_submit": True},
            }
            for i, source in enumerate(snapshot["records"])
        ],
    }
    return snapshot, review


def test_export_is_grouped_sealed_and_explicitly_not_human_ground_truth():
    snapshot, review = _inputs()
    result = curation.build_export(snapshot, review)
    assert len(result["records"]) == 25
    assert len(result["split"]["train_ids"]) == 15
    assert len(result["split"]["selection_ids"]) == 5
    assert result["split"]["sealed_test"]["count"] == 5
    assert result["split"]["seed"] == 42
    assert result["split"]["grouping"] == "session-project-family"
    assert "project_id" not in result["records"][0]["provenance"]
    assert result["curation"]["promotion_eligible"] is False
    assert result["curation"]["expectation_origin"] == "agent-reviewed-draft"
    assert result["export_sha256"] == curation.digest({k: v for k, v in result.items() if k != "export_sha256"})


def test_human_export_requires_complete_opaque_review_and_marks_alignment():
    snapshot, review = _inputs()
    human_review = {
        "schema": curation.HUMAN_REVIEW_SCHEMA,
        "reviewer": "human",
        "reviewer_id": "reviewer-20260914",
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "records": [
            {
                **record,
                "review_status": "human_corrected" if index == 0 else "human_approved",
            }
            for index, record in enumerate(review["records"])
        ],
    }
    result = curation.build_human_aligned_export(snapshot, human_review)

    assert result["curation"]["expectation_origin"] == "human-aligned"
    assert result["curation"]["reviewer_id"] == "reviewer-20260914"
    assert result["curation"]["promotion_eligible"] is False
    assert len(result["records"]) == 25
    assert result["split"]["seed"] == 42


@pytest.mark.parametrize("mutation", ["schema", "reviewer", "reviewer_id", "missing", "pending"])
def test_human_export_rejects_untrusted_or_incomplete_review(mutation):
    snapshot, review = _inputs()
    human_review = {
        "schema": curation.HUMAN_REVIEW_SCHEMA,
        "reviewer": "human",
        "reviewer_id": "reviewer-20260914",
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "records": [dict(record, review_status="human_approved") for record in review["records"]],
    }
    if mutation == "schema":
        human_review["schema"] = "fleet.phase6-curation-review/v1"
    elif mutation == "reviewer":
        human_review["reviewer"] = "agent"
    elif mutation == "reviewer_id":
        human_review["reviewer_id"] = "reviewer/private"
    elif mutation == "missing":
        human_review["records"] = human_review["records"][:-1]
    else:
        human_review["records"][0]["review_status"] = "pending"
    with pytest.raises(ValueError):
        curation.build_human_aligned_export(snapshot, human_review)


def test_human_export_rejects_duplicate_source_identities():
    snapshot, review = _inputs()
    snapshot["records"].append(deepcopy(snapshot["records"][0]))
    snapshot["snapshot_sha256"] = curation.digest(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )
    human_review = {
        "schema": curation.HUMAN_REVIEW_SCHEMA,
        "reviewer": "human",
        "reviewer_id": "reviewer-20260914",
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "records": [dict(record, review_status="human_approved") for record in review["records"]],
    }
    with pytest.raises(ValueError, match="duplicate source identities"):
        curation.build_human_aligned_export(snapshot, human_review)


@pytest.mark.parametrize(
    "mutation", ["query", "seal", "review-seal", "reviewer", "unreviewed", "context", "criteria", "duplicate"]
)
def test_export_rejects_unreviewed_incomplete_or_changed_sources(mutation):
    snapshot, review = _inputs()
    if mutation == "query":
        review["records"][0]["source_query_sha256"] = "a" * 64
    elif mutation == "seal":
        snapshot["records"][0]["query"] = "changed"
    elif mutation == "review-seal":
        review["source_snapshot_sha256"] = "a" * 64
    elif mutation == "reviewer":
        review["reviewer"] = "unreviewed-model-output"
    elif mutation == "unreviewed":
        review["records"][0]["review_status"] = "pending"
    elif mutation == "context":
        review["records"][0]["self_contained"] = False
    elif mutation == "criteria":
        review["records"][0]["expectations"]["criteria"] = []
    else:
        review["records"][-1] = deepcopy(review["records"][0])
    with pytest.raises(ValueError):
        curation.build_export(snapshot, review)


@pytest.mark.parametrize(
    "query",
    ["password=my-private-value", "[content suppressed]", "[redacted]", "", "x" * 50001, "truncated request..."],
)
def test_capture_rejects_sensitive_suppressed_or_incomplete_requests(query):
    trace = _trace()
    trace.data.spans[0].inputs["request"] = query
    with pytest.raises(ValueError):
        curation.capture_record(trace)


def test_capture_requires_real_root_and_session_group():
    trace = _trace()
    trace.info.trace_metadata = {}
    with pytest.raises(ValueError, match="Session"):
        curation.capture_record(trace)
    trace = _trace()
    trace.data.spans.append(trace.data.spans[0])
    with pytest.raises(ValueError, match="unique Fleet root"):
        curation.capture_record(trace)


def test_capture_does_not_include_answers_or_raw_source_identity():
    trace = _trace()
    trace.data.spans[0].outputs = {"answer": "untrusted answer"}
    result = curation.capture_record(trace)
    assert "untrusted answer" not in str(result)
    assert trace.info.trace_id not in str(result)
    assert result["query"] == "Compute 0 plus 1."
