"""Unit contracts for safe GEPA preflight behavior."""

from __future__ import annotations

import json

import pytest

from fleet_rlm.optimization.dataset import EXPORT_SCHEMA
from fleet_rlm.optimization.gepa_runner import (
    CandidateRoundBudget,
    OptimizationPreflightError,
    initialize_preflight_evidence,
    preflight,
    require_live_execution_capability,
)


def _export() -> dict:
    return {
        "schema": EXPORT_SCHEMA,
        "records": [
            {
                "record_id": f"r-{index:03d}",
                "task": {"query": f"synthetic question {index}"},
                "output_contract": {"schema": "answer-v1"},
                "expectations": {"expected_response": "synthetic"},
                "provenance": {"redaction_version": "v1"},
            }
            for index in range(25)
        ],
    }


def test_preflight_translates_candidate_rounds_and_hides_sealed_ids(tmp_path) -> None:
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(_export()), encoding="utf-8")

    receipt = preflight(export_path=export_path, split_seed=9, max_total_cost_usd=12.5)

    assert receipt["gepa_evaluator_call_budget"] == {"exploration": 40, "continuation": 120, "total": 160}
    assert receipt["release_blocked"] is True
    assert "r-" not in str(receipt["split"]["sealed_test"])


def test_preflight_requires_explicit_cost_cap_and_immutable_evidence(tmp_path) -> None:
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(_export()), encoding="utf-8")
    with pytest.raises(OptimizationPreflightError, match="cost"):
        preflight(export_path=export_path, split_seed=0, max_total_cost_usd=None)

    receipt = preflight(export_path=export_path, split_seed=0, max_total_cost_usd=1.0)
    evidence = initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)
    assert (evidence / "manifest.json").is_file()
    with pytest.raises(Exception, match="already exists"):
        initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)


def test_live_execution_fails_closed_until_isolation_is_proven() -> None:
    with pytest.raises(OptimizationPreflightError, match="blocked"):
        require_live_execution_capability()
    assert CandidateRoundBudget().evaluator_calls(selection_records=5)["total"] == 160
