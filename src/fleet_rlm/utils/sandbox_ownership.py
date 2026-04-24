"""Helpers for owner-scoping Daytona sandboxes."""

from __future__ import annotations

from collections.abc import Mapping

from .identity import owner_fingerprint, sanitize_id

SANDBOX_OWNER_LABEL = "fleet-rlm-owner"
SANDBOX_TENANT_LABEL = "fleet-rlm-tenant"
SANDBOX_USER_LABEL = "fleet-rlm-user"
SANDBOX_SESSION_LABEL = "fleet-rlm-session"


def sandbox_owner_labels(
    *,
    tenant_claim: str,
    user_claim: str,
    session_id: str | None = None,
) -> dict[str, str]:
    """Build stable labels used to scope Daytona sandbox operations."""
    labels = {
        SANDBOX_OWNER_LABEL: owner_fingerprint(tenant_claim, user_claim),
        SANDBOX_TENANT_LABEL: sanitize_id(tenant_claim, "default"),
        SANDBOX_USER_LABEL: sanitize_id(user_claim, "anonymous"),
    }
    if session_id:
        labels[SANDBOX_SESSION_LABEL] = sanitize_id(session_id, "default-session")
    return labels


def sandbox_owner_matches(
    labels: Mapping[str, object] | None,
    *,
    owner_label: str,
) -> bool:
    """Return whether sandbox labels identify the expected owner."""
    if not labels:
        return False
    return str(labels.get(SANDBOX_OWNER_LABEL, "") or "") == owner_label


def sandbox_has_owner_label(labels: Mapping[str, object] | None) -> bool:
    """Return whether a sandbox carries the owner label introduced by fleet-rlm."""
    return bool(labels and str(labels.get(SANDBOX_OWNER_LABEL, "") or "").strip())


__all__ = [
    "SANDBOX_OWNER_LABEL",
    "SANDBOX_SESSION_LABEL",
    "SANDBOX_TENANT_LABEL",
    "SANDBOX_USER_LABEL",
    "sandbox_has_owner_label",
    "sandbox_owner_labels",
    "sandbox_owner_matches",
]
