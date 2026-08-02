"""CLI contracts for the bounded development GEPA smoke."""

from __future__ import annotations

import pytest

from fleet_rlm.optimization.evidence import EvidenceError
from fleet_rlm.optimization.gepa_runner import OptimizationPreflightError, run_development_smoke
from scripts.optimize import optimize_signature_gepa as cli


def test_cli_loads_dotenv_and_sanitizes_existing_evidence(monkeypatch, capsys) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls: list[tuple[object, bool]] = []

    def fake_load_dotenv(path, *, override: bool) -> bool:
        calls.append((path, override))
        return True

    def fail_existing_run(**_kwargs):
        raise EvidenceError("evidence run already exists: secret-path")

    monkeypatch.setattr(cli, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(cli, "run_development_smoke", fail_existing_run)

    exit_code = cli.main(
        [
            "development-smoke",
            "--export-json",
            "development-synthetic-export.json",
            "--max-total-cost-usd",
            "0.10",
            "--run-id",
            "existing-run",
        ]
    )

    assert exit_code == 2
    assert calls == [(cli._REPO_ROOT / ".env", False)]
    assert capsys.readouterr().err.strip() == '{"status": "blocked", "error_category": "EvidenceError"}'


def test_development_smoke_requires_live_opt_in_before_loading_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("FLEET_LIVE", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.optimization.gepa_runner._require_cost_cap",
        lambda _value: pytest.fail("cost validation must not run before live opt-in"),
    )

    with pytest.raises(OptimizationPreflightError, match="FLEET_LIVE=1"):
        run_development_smoke(
            export_path=tmp_path / "missing.json",
            split_seed=0,
            max_total_cost_usd=0.10,
            max_evals=1,
            evidence_root=tmp_path / "evidence",
            run_id="no-live",
        )
