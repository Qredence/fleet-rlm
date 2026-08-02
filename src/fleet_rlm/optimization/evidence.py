"""Write-once evidence storage for safe optimization runs."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_STRICT_PROOF_SCHEMA = "fleet.strict-daytona-proof/v1"
_STRICT_PROOF_PATH = "strict-daytona-proof.json"
_DEVELOPMENT_CANARY_SCHEMA = "fleet.daytona-development-canary/v1"
_DEVELOPMENT_CANARY_PATH = "daytona-development-canary.json"
_REQUIRED_CONTROLS = frozenset(
    {
        "no_volume_requested",
        "ephemeral_requested",
        "domain_allow_list_requested",
        "auto_stop_seconds",
        "auto_delete_seconds",
    }
)
_REQUIRED_OUTCOMES = frozenset(
    {
        "broker_started",
        "broker_round_trip",
        "valid_capability_read",
        "invalid_transaction_denied",
        "invalid_digest_denied",
        "direct_egress_denied",
        "denied_egress_unobserved",
        "effective_policy_verified",
        "host_credentials_absent",
        "interpreter_cleanup",
        "broker_cleanup",
        "sandbox_deleted",
    }
)
_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "candidate",
        "record",
        "transaction",
        "handle",
        "sandbox_id",
        "preview",
        "token",
        "secret",
        "credential",
        "environment",
        "provider_error",
        "stack",
        "trace",
        "body",
        "payload",
    }
)
_VALIDATED_PROOF_ISSUER = object()


class EvidenceError(RuntimeError):
    """Evidence storage violates its immutable-run contract."""


class StrictDaytonaProofError(EvidenceError):
    """A strict Daytona receipt is malformed, unsafe, or insufficient."""


@dataclass(frozen=True, slots=True)
class DevelopmentDaytonaCanaryReport:
    """Sanitized development-only egress-canary report.

    Temporary tunnel hostnames deliberately do not appear in this report.  The
    report is evidence for the local smoke harness only; unlike a strict proof,
    it cannot authorize evaluator execution.
    """

    policy_id: str
    snapshot: str
    controls: Mapping[str, bool | int]
    outcomes: Mapping[str, str]
    schema: str = _DEVELOPMENT_CANARY_SCHEMA

    def public_payload(self) -> dict[str, Any]:
        """Return a stable, URL-free report representation."""
        payload = {
            "schema": self.schema,
            "policy_id": self.policy_id,
            "snapshot": self.snapshot,
            "controls": dict(self.controls),
            "outcomes": dict(self.outcomes),
        }
        return {**payload, "report_id": _canonical_digest(payload)}


@dataclass(frozen=True, slots=True)
class StrictDaytonaProofReceipt:
    """Sanitized behavioral proof for one exact strict Daytona policy.

    This deliberately stores policy and bounded outcomes only. It is not a log of
    an evaluator run and therefore must never contain sandbox identity, curated
    input, broker credentials, preview information, or provider diagnostics.
    """

    policy_id: str
    snapshot: str
    gateway_domains: tuple[str, ...]
    controls: Mapping[str, bool | int]
    outcomes: Mapping[str, str]
    schema: str = _STRICT_PROOF_SCHEMA

    def canonical_payload(self) -> dict[str, Any]:
        """Return the deterministic, non-secret representation used for hashing."""
        return {
            "schema": self.schema,
            "policy_id": self.policy_id,
            "snapshot": self.snapshot,
            "gateway_domains": list(self.gateway_domains),
            "controls": dict(self.controls),
            "outcomes": dict(self.outcomes),
        }

    @property
    def proof_id(self) -> str:
        return _canonical_digest(self.canonical_payload())

    def public_payload(self) -> dict[str, Any]:
        """Return the persisted receipt, including its integrity identifier."""
        return {**self.canonical_payload(), "proof_id": self.proof_id}


@dataclass(frozen=True, slots=True, init=False)
class ValidatedStrictDaytonaProof:
    """Receipt-derived authority required to admit strict evaluator execution."""

    receipt: StrictDaytonaProofReceipt
    _issuer: object = field(repr=False, compare=False)

    def __init__(self, receipt: StrictDaytonaProofReceipt, issuer: object) -> None:
        if issuer is not _VALIDATED_PROOF_ISSUER:
            raise StrictDaytonaProofError("strict Daytona proof must be issued by the receipt validator")
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(self, "_issuer", issuer)

    @property
    def proof_id(self) -> str:
        return self.receipt.proof_id

    def require_matches(
        self,
        *,
        policy_id: str,
        snapshot: str,
        gateway_domains: tuple[str, ...],
        auto_stop_interval_seconds: int,
        auto_delete_interval_seconds: int,
    ) -> None:
        """Reject use of proof outside the exact policy it live-tested."""
        receipt = self.receipt
        if (
            receipt.policy_id != policy_id
            or receipt.snapshot != snapshot
            or receipt.gateway_domains != gateway_domains
            or receipt.controls["auto_stop_seconds"] != auto_stop_interval_seconds
            or receipt.controls["auto_delete_seconds"] != auto_delete_interval_seconds
        ):
            raise StrictDaytonaProofError("strict Daytona proof does not match evaluator policy")


def validate_strict_daytona_proof(receipt: StrictDaytonaProofReceipt) -> ValidatedStrictDaytonaProof:
    """Validate a complete sanitized proof and return execution authority."""
    if not isinstance(receipt, StrictDaytonaProofReceipt):
        raise StrictDaytonaProofError("strict Daytona proof must use the versioned receipt type")
    payload = receipt.canonical_payload()
    _reject_sensitive_payload(payload)
    if receipt.schema != _STRICT_PROOF_SCHEMA:
        raise StrictDaytonaProofError("unsupported strict Daytona proof schema")
    if not _safe_identifier(receipt.policy_id) or not isinstance(receipt.snapshot, str) or not receipt.snapshot.strip():
        raise StrictDaytonaProofError("strict Daytona proof policy binding is invalid")
    if tuple(sorted(set(receipt.gateway_domains))) != receipt.gateway_domains or not receipt.gateway_domains:
        raise StrictDaytonaProofError("strict Daytona proof gateway domains must be normalized")
    if set(receipt.controls) != _REQUIRED_CONTROLS:
        raise StrictDaytonaProofError("strict Daytona proof controls are incomplete")
    controls = receipt.controls
    if any(controls[key] is not True for key in _REQUIRED_CONTROLS if key.endswith("_requested")):
        raise StrictDaytonaProofError("strict Daytona proof did not request all mandatory controls")
    if not _valid_ephemeral_lifecycle_controls(controls):
        raise StrictDaytonaProofError("strict Daytona proof lifecycle controls are invalid")
    if set(receipt.outcomes) != _REQUIRED_OUTCOMES | {"approved_gateway_egress"}:
        raise StrictDaytonaProofError("strict Daytona proof outcomes are incomplete")
    if any(receipt.outcomes[key] != "passed" for key in _REQUIRED_OUTCOMES):
        raise StrictDaytonaProofError("strict Daytona proof has a failed mandatory outcome")
    if receipt.outcomes["approved_gateway_egress"] != "passed":
        raise StrictDaytonaProofError("strict Daytona proof gateway outcome did not pass")
    return ValidatedStrictDaytonaProof(receipt, _VALIDATED_PROOF_ISSUER)


def write_strict_daytona_proof(store: EvidenceStore, receipt: StrictDaytonaProofReceipt) -> ValidatedStrictDaytonaProof:
    """Validate and immutably persist a receipt before returning its authority."""
    proof = validate_strict_daytona_proof(receipt)
    store.write_json(_STRICT_PROOF_PATH, receipt.public_payload())
    return proof


def write_development_daytona_canary_report(
    store: EvidenceStore,
    report: DevelopmentDaytonaCanaryReport,
) -> None:
    """Persist development smoke evidence without creating execution authority."""
    if not isinstance(report, DevelopmentDaytonaCanaryReport):
        raise StrictDaytonaProofError("development Daytona canary must use the versioned report type")
    if report.schema != _DEVELOPMENT_CANARY_SCHEMA:
        raise StrictDaytonaProofError("unsupported development Daytona canary schema")
    if not _safe_identifier(report.policy_id) or not isinstance(report.snapshot, str) or not report.snapshot.strip():
        raise StrictDaytonaProofError("development Daytona canary policy binding is invalid")
    if set(report.controls) != _REQUIRED_CONTROLS:
        raise StrictDaytonaProofError("development Daytona canary controls are incomplete")
    if not _valid_ephemeral_lifecycle_controls(report.controls):
        raise StrictDaytonaProofError("development Daytona canary lifecycle controls are invalid")
    if set(report.outcomes) != _REQUIRED_OUTCOMES | {"approved_gateway_egress"}:
        raise StrictDaytonaProofError("development Daytona canary outcomes are incomplete")
    _reject_sensitive_payload(report.public_payload())
    store.write_json(_DEVELOPMENT_CANARY_PATH, report.public_payload())


def _valid_ephemeral_lifecycle_controls(controls: Mapping[str, bool | int]) -> bool:
    """Require the actual lifecycle sent to Daytona for an ephemeral sandbox."""
    return (
        isinstance(controls["auto_stop_seconds"], int)
        and isinstance(controls["auto_delete_seconds"], int)
        and controls["auto_stop_seconds"] >= 1
        and controls["auto_delete_seconds"] == 0
    )


class EvidenceStore:
    """Persist sanitized run evidence without overwriting artifacts."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root / run_id
        self._initialized = False

    def initialize(self, manifest: dict[str, Any]) -> Path:
        """Create an empty run directory and its immutable manifest."""
        if self.root.exists():
            raise EvidenceError(f"evidence run already exists: {self.root}")
        self.root.mkdir(mode=0o700, parents=True)
        for relative in ("candidates", "records", "logs"):
            (self.root / relative).mkdir(mode=0o700)
        self.write_json("manifest.json", manifest)
        self._initialized = True
        return self.root

    def write_json(self, relative_path: str, payload: dict[str, Any]) -> Path:
        """Write a JSON artifact once using an atomic no-replace finalization."""
        if not self._initialized and relative_path != "manifest.json":
            raise EvidenceError("initialize evidence before writing artifacts")
        target = self._target(relative_path)
        content = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        self._write_once(target, content.encode())
        return target

    def write_text(self, relative_path: str, content: str) -> Path:
        """Write a text artifact once using an atomic no-replace finalization."""
        if not self._initialized:
            raise EvidenceError("initialize evidence before writing artifacts")
        target = self._target(relative_path)
        self._write_once(target, content.encode())
        return target

    @staticmethod
    def digest(content: str) -> str:
        """Return the canonical content digest used in manifests."""
        return hashlib.sha256(content.encode()).hexdigest()

    def _target(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise EvidenceError("evidence path escapes run directory")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return target

    @staticmethod
    def _write_once(target: Path, content: bytes) -> None:
        if target.exists():
            raise EvidenceError(f"refusing to overwrite evidence: {target.name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError as exc:
            raise EvidenceError(f"refusing to overwrite evidence: {target.name}") from exc
        with os.fdopen(descriptor, "wb") as artifact:
            artifact.write(content)
            artifact.flush()
            os.fsync(artifact.fileno())


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _safe_identifier(value: str) -> bool:
    return bool(value) and len(value) <= 128 and all(character.isalnum() or character in "._-" for character in value)


def _reject_sensitive_payload(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise StrictDaytonaProofError("strict Daytona proof keys must be strings")
            lowered = nested_key.lower()
            if nested_key not in _REQUIRED_OUTCOMES and any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise StrictDaytonaProofError("strict Daytona proof contains a forbidden sensitive field")
            _reject_sensitive_payload(nested_value, key=nested_key)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _reject_sensitive_payload(nested, key=key)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if "http://" in lowered or "https://" in lowered or "/users/" in lowered or "/home/" in lowered:
            raise StrictDaytonaProofError("strict Daytona proof contains unsafe detail")


__all__ = [
    "DevelopmentDaytonaCanaryReport",
    "EvidenceError",
    "EvidenceStore",
    "StrictDaytonaProofError",
    "StrictDaytonaProofReceipt",
    "ValidatedStrictDaytonaProof",
    "validate_strict_daytona_proof",
    "write_development_daytona_canary_report",
    "write_strict_daytona_proof",
]
