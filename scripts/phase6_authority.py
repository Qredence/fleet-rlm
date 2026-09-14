"""Phase 6 promotion issuance authority primitives."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase6_identity import (
    _DECISION_GATES,
    _SHA256,
    PromotionBundleError,
    _canonical_bytes,
    _require_digest,
)

_AUTHORITY_ISSUER = object()


class _IssuedMapping(dict[str, Any]):
    """A JSON-compatible receipt that retains an in-process issuance seal.

    Persisting a mapping intentionally drops this marker.  Promotion authority
    therefore requires the object returned directly by the owning validator;
    a caller-resealed JSON object remains inspection-only.  The digest also
    detects mutation after issuance.
    """

    __slots__ = ("_issuer", "_kind", "_seal")

    def __init__(self, payload: Mapping[str, Any], kind: str) -> None:
        super().__init__(payload)
        self._kind = kind
        self._issuer = _AUTHORITY_ISSUER
        self._seal = hashlib.sha256(_canonical_bytes(dict(self))).hexdigest()


def _issue_mapping(payload: Mapping[str, Any], kind: str) -> _IssuedMapping:
    return _IssuedMapping(payload, kind)


def _is_issued(value: object, kind: str) -> bool:
    return (
        isinstance(value, _IssuedMapping)
        and value._issuer is _AUTHORITY_ISSUER
        and value._kind == kind
        and value._seal == hashlib.sha256(_canonical_bytes(dict(value))).hexdigest()
    )


@dataclass(frozen=True, slots=True, init=False)
class ValidatedGateEvidence:
    """An owner-issued identity for one promotion gate.

    The public decision builder accepts this object rather than a caller
    boolean.  Its private issuer is deliberately not serializable, so a
    persisted attestation cannot silently become promotion authority.
    """

    gate: str
    candidate_bundle_sha256: str
    evidence_sha256: str
    _issuer: object = dataclass_field(repr=False, compare=False)

    def __init__(self, gate: str, candidate_bundle_sha256: str, evidence_sha256: str, issuer: object) -> None:
        if issuer is not _AUTHORITY_ISSUER:
            raise PromotionBundleError("promotion gate evidence must be issued by its owner")
        object.__setattr__(self, "gate", gate)
        object.__setattr__(self, "candidate_bundle_sha256", candidate_bundle_sha256)
        object.__setattr__(self, "evidence_sha256", evidence_sha256)
        object.__setattr__(self, "_issuer", issuer)


def issue_gate_evidence(gate: str, candidate_bundle_sha256: str, evidence_sha256: str) -> ValidatedGateEvidence:
    """Issue a candidate-bound gate identity from an owning validator.

    This small seam is intentionally the only constructor exposed to
    composition code.  Callers should invoke it only after the owner has
    validated the underlying artifact (for example a scorer or database
    compatibility receipt); the resulting object is not serializable and a
    JSON copy cannot authorize a promotion decision.
    """
    _require_digest(candidate_bundle_sha256, "candidate_bundle_sha256")
    _require_digest(evidence_sha256, "evidence_sha256")
    if gate not in _DECISION_GATES:
        raise PromotionBundleError("unsupported promotion gate evidence")
    return ValidatedGateEvidence(gate, candidate_bundle_sha256, evidence_sha256, _AUTHORITY_ISSUER)


def _gate_is_authorized(
    evidence: ValidatedGateEvidence | None,
    *,
    gate: str,
    candidate_bundle_sha256: str,
) -> bool:
    return (
        isinstance(evidence, ValidatedGateEvidence)
        and evidence._issuer is _AUTHORITY_ISSUER
        and evidence.gate == gate
        and evidence.candidate_bundle_sha256 == candidate_bundle_sha256
        and _SHA256.fullmatch(evidence.evidence_sha256) is not None
    )


def _controller_preflight_is_authorized(value: object, pair: Mapping[str, Any] | None) -> bool:
    if (
        not isinstance(pair, Mapping)
        or not _is_issued(value, "controller-switch-preflight")
        or not isinstance(value, dict)
    ):
        return False
    try:
        transition_sha256 = _require_digest(value.get("controller_transition_sha256"), "controller_transition_sha256")
        controller_bundle_sha256 = _require_digest(value.get("controller_bundle_sha256"), "controller_bundle_sha256")
        compatibility_sha256 = _require_digest(
            value.get("database_compatibility_sha256"), "database_compatibility_sha256"
        )
    except PromotionBundleError:
        return False
    return (
        bool(transition_sha256)
        and value.get("controller_stage") in {"baseline", "candidate"}
        and controller_bundle_sha256 in {pair["baseline_bundle_sha256"], pair["candidate_bundle_sha256"]}
        and bool(compatibility_sha256)
    )


def _quiescence_observation_digest(observation: Mapping[str, Any]) -> str:
    """Match a switch observation to the controller's sanitized state hash."""
    return hashlib.sha256(
        _canonical_bytes(
            {
                "admissions_closed": observation.get("admissions_closed"),
                "active_runs": observation.get("active_runs"),
                "active_workers": observation.get("active_workers"),
                "pending_cleanup": observation.get("pending_cleanup"),
                "provider_cleanup_confirmed": observation.get("provider_cleanup_confirmed"),
                "database_compatibility_sha256": observation.get("database_compatibility_sha256"),
            }
        )
    ).hexdigest()


def _database_compatibility_is_authorized(
    evidence: ValidatedGateEvidence | None,
    *,
    candidate_bundle_sha256: str,
    switch_preflight: Mapping[str, Any],
) -> bool:
    """Require DB evidence to match the controller-bound switch observation."""
    if not _gate_is_authorized(
        evidence,
        gate="database_compatibility",
        candidate_bundle_sha256=candidate_bundle_sha256,
    ):
        return False
    expected = switch_preflight.get("database_compatibility_sha256")
    return evidence is not None and isinstance(expected, str) and evidence.evidence_sha256 == expected


def _trusted_scorer_is_authorized(
    evidence: ValidatedGateEvidence | None,
    *,
    candidate_bundle_sha256: str,
    scorer_sha256: str,
) -> bool:
    return (
        _gate_is_authorized(
            evidence,
            gate="trusted_scorer",
            candidate_bundle_sha256=candidate_bundle_sha256,
        )
        and evidence is not None
        and evidence.evidence_sha256 == scorer_sha256
    )


__all__ = [
    "ValidatedGateEvidence",
    "issue_gate_evidence",
]
