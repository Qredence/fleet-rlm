"""Provider-independent validation for Fleet Snapshot identities."""

from __future__ import annotations

import re

_VERSIONED_SNAPSHOT = re.compile(r"^[a-z0-9][a-z0-9-]*-v[1-9][0-9]*$")
_MUTABLE_SNAPSHOT_NAMES = frozenset({"latest", "stable", "lts"})


def validate_snapshot_name(value: str) -> str:
    """Normalize and validate the one immutable Snapshot naming policy."""
    name = value.strip()
    if not name:
        raise ValueError("Daytona snapshot name is required")
    if name.lower() in _MUTABLE_SNAPSHOT_NAMES or not _VERSIONED_SNAPSHOT.fullmatch(name):
        raise ValueError("Daytona snapshot name must be immutable and end in -v<positive integer>")
    return name
