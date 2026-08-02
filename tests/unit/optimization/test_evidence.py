"""Unit contracts for sanitized strict Daytona proof receipts."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from fleet_rlm.optimization.evidence import (
    DevelopmentDaytonaCanaryReport,
    EvidenceError,
    EvidenceStore,
    StrictDaytonaProofError,
    StrictDaytonaProofReceipt,
    validate_strict_daytona_proof,
    write_development_daytona_canary_report,
    write_strict_daytona_proof,
)


def _receipt(**changes: object) -> StrictDaytonaProofReceipt:
    values: dict[str, object] = {
        "policy_id": "a" * 64,
        "snapshot": "fleet-safe-v1",
        "gateway_domains": ("gateway.example.test",),
        "controls": {
            "no_volume_requested": True,
            "ephemeral_requested": True,
            "domain_allow_list_requested": True,
            "auto_stop_seconds": 300,
            "auto_delete_seconds": 0,
        },
        "outcomes": {
            "broker_started": "passed",
            "broker_round_trip": "passed",
            "valid_capability_read": "passed",
            "invalid_transaction_denied": "passed",
            "invalid_digest_denied": "passed",
            "direct_egress_denied": "passed",
            "denied_egress_unobserved": "passed",
            "effective_policy_verified": "passed",
            "host_credentials_absent": "passed",
            "interpreter_cleanup": "passed",
            "broker_cleanup": "passed",
            "sandbox_deleted": "passed",
            "approved_gateway_egress": "passed",
        },
    }
    values.update(changes)
    return StrictDaytonaProofReceipt(**cast(Any, values))


def test_validated_receipt_has_stable_non_secret_proof_id() -> None:
    first = validate_strict_daytona_proof(_receipt())
    second = validate_strict_daytona_proof(_receipt())

    assert first.proof_id == second.proof_id
    assert len(first.proof_id) == 64
    assert first.receipt.public_payload()["proof_id"] == first.proof_id


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"outcomes": {**_receipt().outcomes, "direct_egress_denied": "failed"}}, "failed mandatory"),
        ({"outcomes": {**_receipt().outcomes, "denied_egress_unobserved": "failed"}}, "failed mandatory"),
        ({"outcomes": {**_receipt().outcomes, "host_credentials_absent": "failed"}}, "failed mandatory"),
        ({"controls": {**_receipt().controls, "no_volume_requested": False}}, "mandatory controls"),
        ({"outcomes": {**_receipt().outcomes, "approved_gateway_egress": "not_configured"}}, "gateway outcome"),
        ({"snapshot": ""}, "policy binding"),
    ],
)
def test_validator_rejects_incomplete_or_failed_proofs(change: dict[str, object], message: str) -> None:
    with pytest.raises(StrictDaytonaProofError, match=message):
        validate_strict_daytona_proof(_receipt(**change))


@pytest.mark.parametrize("field", ["transaction_id", "candidate_text", "preview_url", "provider_error"])
def test_validator_rejects_sensitive_receipt_fields(field: str) -> None:
    with pytest.raises(StrictDaytonaProofError, match="forbidden sensitive"):
        validate_strict_daytona_proof(_receipt(controls={**_receipt().controls, field: True}))


def test_receipt_writes_once_after_store_initialization(tmp_path) -> None:
    store = EvidenceStore(tmp_path, "run-1")
    store.initialize({"schema": "test"})

    proof = write_strict_daytona_proof(store, _receipt())
    receipt_path = store.root / "strict-daytona-proof.json"

    assert json.loads(receipt_path.read_text())["proof_id"] == proof.proof_id
    with pytest.raises(EvidenceError, match="overwrite"):
        write_strict_daytona_proof(store, _receipt())


def test_development_canary_report_cannot_issue_production_proof(tmp_path) -> None:
    report = DevelopmentDaytonaCanaryReport(
        policy_id="a" * 64,
        snapshot="fleet-safe-v1",
        controls=_receipt().controls,
        outcomes=_receipt().outcomes,
    )
    store = EvidenceStore(tmp_path, "development-canary")
    store.initialize({"schema": "test"})

    write_development_daytona_canary_report(store, report)

    payload = json.loads((store.root / "daytona-development-canary.json").read_text())
    assert "gateway_domains" not in payload
    assert "proof_id" not in payload
    with pytest.raises(StrictDaytonaProofError, match="versioned receipt"):
        validate_strict_daytona_proof(cast(StrictDaytonaProofReceipt, report))
