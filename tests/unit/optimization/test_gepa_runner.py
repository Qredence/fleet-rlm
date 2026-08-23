"""Unit contracts for safe GEPA preflight behavior.

The optimizer path targets official ``gepa==0.1.4`` surfaces only: the retired
omni API and the unsupported USD reflection-cost cap are gone, and the only
budget contract is the official bounded metric-call one (bounded overshoot
accepted per mission decision).
"""

from __future__ import annotations

import inspect
import json

import pytest

from fleet_rlm.optimization.dataset import EXPORT_SCHEMA
from fleet_rlm.optimization.gepa_runner import (
    CandidateRoundBudget,
    OptimizationPreflightError,
    initialize_preflight_evidence,
    preflight,
    require_live_execution_capability,
    run_development_smoke,
)

_USD_CAP_MARKERS = ("max_total_cost_usd", "max_token_cost", "max-total-cost-usd", "max_reflection_cost")


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


def _write_export(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(_export()), encoding="utf-8")
    return export_path


def test_preflight_translates_candidate_rounds_and_hides_sealed_ids(tmp_path) -> None:
    receipt = preflight(export_path=_write_export(tmp_path), split_seed=9)

    assert receipt["gepa_evaluator_call_budget"] == {"exploration": 40, "continuation": 120, "total": 160}
    assert receipt["release_blocked"] is True
    assert "r-" not in str(receipt["split"]["sealed_test"])
    for marker in _USD_CAP_MARKERS:
        assert marker not in str(receipt)


def test_preflight_rejects_obsolete_usd_reflection_cost_cap(tmp_path) -> None:
    """The unsupported USD reflection-cost cap is removed, not tolerated."""
    export_path = _write_export(tmp_path)

    with pytest.raises(TypeError):
        preflight(export_path=export_path, split_seed=0, max_total_cost_usd=1.0)  # type: ignore[call-arg]

    for entrypoint in (preflight, run_development_smoke):
        parameters = inspect.signature(entrypoint).parameters
        for marker in ("max_total_cost_usd", "max_token_cost", "max_reflection_cost"):
            assert marker not in parameters


def test_run_development_smoke_rejects_obsolete_usd_reflection_cost_cap(tmp_path) -> None:
    with pytest.raises(TypeError):
        run_development_smoke(  # type: ignore[call-arg]
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=1,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
            max_total_cost_usd=1.0,
        )


def test_smoke_requires_bounded_metric_call_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    with pytest.raises(OptimizationPreflightError, match="max_metric_calls"):
        run_development_smoke(
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=0,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
        )


def test_smoke_gepa_dependency_failure_is_bounded_and_actionable(tmp_path, monkeypatch) -> None:
    """VAL-PKG-024: a missing optimizer dependency fails closed with an install hint."""
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.invalid")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dummy")

    real_import = __import__

    def _blocked_import(name, *args, **kwargs):
        if name == "gepa" or name.startswith("gepa."):
            raise ImportError("No module named 'gepa'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocked_import)

    with pytest.raises(OptimizationPreflightError, match=r"fleet-rlm\[optimize\]") as excinfo:
        run_development_smoke(
            export_path=_write_export(tmp_path),
            split_seed=0,
            max_metric_calls=1,
            evidence_root=tmp_path / "evidence",
            run_id="run-1",
        )
    # No import traceback escapes the boundary.
    assert "Traceback" not in str(excinfo.value)


def test_development_smoke_runs_official_gepa_with_bounded_metric_calls(tmp_path) -> None:
    """Official surface: gepa.optimize + custom adapter, no retired API, no USD cap.

    The reflection model is a deterministic stub, so the run needs no provider.
    The observed metric-call count is recorded through the official
    ``GEPAResult.total_metric_calls`` counter and stays within the requested
    cap plus the documented bounded overshoot (one in-flight evaluation batch).
    """
    gepa = pytest.importorskip("gepa")
    from fleet_rlm.optimization.gepa_runner import _DevelopmentInstructionAdapter

    seed_text = "verify typed answers and submit them with python."
    stub_proposal = "verify typed outputs, submit via python tools, and keep answers concise."

    def _stub_reflection_lm(prompt: str) -> str:
        assert isinstance(prompt, str) and prompt.strip()
        return f"```\n{stub_proposal}\n```"

    train = [{"query": f"train question {index}"} for index in range(4)]
    selection = [{"query": f"selection question {index}"} for index in range(2)]
    requested_cap = 1
    result = gepa.optimize(
        seed_candidate={"system_prompt": seed_text},
        trainset=train,
        valset=selection,
        adapter=_DevelopmentInstructionAdapter(),
        reflection_lm=_stub_reflection_lm,
        reflection_minibatch_size=1,
        max_metric_calls=requested_cap,
        run_dir=str(tmp_path / "gepa-run"),
        seed=3,
        track_best_outputs=False,
        display_progress_bar=False,
    )

    observed = result.total_metric_calls
    assert observed is not None and observed >= requested_cap
    # Official bounded overshoot: the stopper is consulted between evaluation
    # batches, so at most one in-flight batch (full valset or minibatch) may
    # land beyond the requested cap.
    allowed = requested_cap + max(len(selection), 1)
    assert observed <= allowed


def test_live_execution_fails_closed_until_isolation_is_proven() -> None:
    with pytest.raises(OptimizationPreflightError, match="blocked"):
        require_live_execution_capability()
    assert CandidateRoundBudget().evaluator_calls(selection_records=5)["total"] == 160


def test_preflight_evidence_is_write_once(tmp_path) -> None:
    receipt = preflight(export_path=_write_export(tmp_path), split_seed=0)
    evidence = initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)
    assert (evidence / "manifest.json").is_file()
    with pytest.raises(Exception, match="already exists"):
        initialize_preflight_evidence(evidence_root=tmp_path, run_id="run-1", receipt=receipt)
