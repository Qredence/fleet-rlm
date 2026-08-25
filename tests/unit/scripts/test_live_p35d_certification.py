from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.live_p35d_certification import (
    CLAIM_LANES,
    LIVE_LANES,
    CertificationError,
    build_manifest,
    scan_host_log_surfaces,
)


def test_claim_lane_table_covers_assigned_contract() -> None:
    assert set(CLAIM_LANES) == {
        "VAL-RLM-001",
        "VAL-RLM-059",
        "VAL-RLM-060",
        "VAL-RLM-061",
        "VAL-RLM-062",
        "VAL-RLM-065",
        "VAL-RLM-071",
    }
    assert all(lanes for lanes in CLAIM_LANES.values())
    assert all(lane in LIVE_LANES for lanes in CLAIM_LANES.values() for lane in lanes)


def test_manifest_rejects_mixed_candidate_identity() -> None:
    receipt = {
        "schema": "fleet.live-lane/v1",
        "passed": True,
        "candidate": {"sha": "a" * 40, "lockfile_sha256": "b" * 64, "dspy": "3.3.1"},
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
    }
    mismatched = dict(receipt)
    mismatched["candidate"] = {
        "sha": "c" * 40,
        "lockfile_sha256": "b" * 64,
        "dspy": "3.3.1",
    }
    with pytest.raises(CertificationError, match="candidate SHA"):
        build_manifest(
            sha="a" * 40,
            lockfile_sha256="b" * 64,
            receipts={"root": receipt, "child": mismatched},
            scans={"host_logs": {"passed": True}},
            runtime={"metadata": "3.3.1", "module": "3.3.1"},
        )


def test_secret_scan_reports_only_bounded_relative_findings(tmp_path: Path) -> None:
    logs = tmp_path / ".fleet_rlm" / "logs"
    logs.mkdir(parents=True)
    canary = "canary-fake-token"
    (logs / "fault.log").write_text(f"provider failed {canary}\n", encoding="utf-8")

    result = scan_host_log_surfaces(tmp_path, secret_values=(canary,))

    assert result["passed"] is False
    assert result["files_scanned"] == 1
    assert result["findings"] == [{"path": ".fleet_rlm/logs/fault.log", "matches": ["secret_value"]}]
    assert canary not in json.dumps(result)


def test_secret_scan_ignores_redacted_and_audience_values(tmp_path: Path) -> None:
    logs = tmp_path / ".fleet_rlm" / "logs"
    logs.mkdir(parents=True)
    (logs / "backend.log").write_text(
        "token=*** token_audience=https://canary.invalid/oidc\nexport DATABRICKS_TOKEN='...'\n",
        encoding="utf-8",
    )

    result = scan_host_log_surfaces(tmp_path, secret_names=("DATABRICKS_TOKEN",))

    assert result["passed"] is True
