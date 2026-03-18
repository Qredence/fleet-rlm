"""Auth domain types for server identity normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedIdentity:
    """Normalized identity extracted from auth claims."""

    tenant_claim: str
    user_claim: str
    email: str | None = None
    name: str | None = None
    raw_claims: dict[str, Any] | None = None
