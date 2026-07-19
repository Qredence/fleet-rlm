"""Reject unauthenticated non-loopback API binds unless explicitly opted in."""

from __future__ import annotations

import ipaddress


class UnsafeBindError(ValueError):
    """Raised when the API would listen beyond loopback without an unsafe opt-in."""


def is_loopback_bind_host(host: str) -> bool:
    """Return True when *host* is a loopback literal safe for the no-auth product."""
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(address.is_loopback)


def require_safe_bind_host(host: str, *, allow_non_loopback: bool) -> None:
    """Reject non-loopback binds unless ``allow_non_loopback`` is True.

    Fleet uses one deterministic local User/Workspace with no caller auth. Binding
    to a wildcard or remote address without an explicit opt-in would expose
    Sessions, workspace operations, Attachments, Artifacts, and BYOK model
    execution on the network.
    """
    if allow_non_loopback or is_loopback_bind_host(host):
        return
    raise UnsafeBindError(
        f"refusing to bind unauthenticated Fleet API to non-loopback host {host!r}; "
        "pass --allow-non-loopback-bind to opt in deliberately"
    )
