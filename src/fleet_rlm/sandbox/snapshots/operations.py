"""Explicit operator operations shared by the setup API."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

from daytona import CreateSnapshotParams, Daytona, DaytonaConfig

from fleet_rlm.config import get_settings

from .definition import DEFAULT_DEFINITION, SNAPSHOT_NAME
from .preflight import (
    SNAPSHOT_BUILD_TIMEOUT_SECONDS,
    SnapshotIdentity,
    SnapshotUnavailableError,
    _is_not_found,
    preflight,
)


def _resolved_credentials(api_key: str | None, org_id: str | None) -> tuple[str, str]:
    """Resolve omitted credentials from Fleet settings and strip surrounding whitespace.

    Raise ValueError when either resolved credential is empty; settings errors propagate.
    """
    if api_key is None or org_id is None:
        settings = get_settings()
        if api_key is None:
            api_key = settings.fleet_daytona_api_key
        if org_id is None:
            org_id = settings.fleet_daytona_org_id
    key = (api_key or "").strip()
    organization = (org_id or "").strip()
    if not key:
        raise ValueError(
            "Daytona API key is not configured: set FLEET_DAYTONA_API_KEY in the environment or .env, "
            "or pass api_key explicitly."
        )
    if not organization:
        raise ValueError(
            "Daytona organization is not configured: set FLEET_DAYTONA_ORG_ID, add org_id to the "
            "[daytona] table of the Fleet TOML configuration, or pass org_id explicitly."
        )
    return key, organization


def build_snapshot(
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str | None = None,
    org_id: str | None = None,
    timeout_seconds: float = SNAPSHOT_BUILD_TIMEOUT_SECONDS,
    on_logs: Callable[[str], None] | None = print,
) -> SnapshotIdentity:
    """Build the repository-defined snapshot and verify it through the provider.

    Omitted credentials come from Fleet settings; `name` overrides only the provider name.
    The build streams logs and waits up to 20 minutes by default. A timeout does not
    guarantee the remote build stopped: inspect provider state before retrying.

    Raises:
        ValueError: Credentials are empty or the build timeout is not finite and positive.
        SnapshotUnavailableError: The created snapshot cannot be read, is not active,
            or is below a resource floor.

    Settings, image-building, and provider creation errors propagate.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Snapshot build timeout_seconds must be finite and positive")
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    try:
        existing = client.snapshot.get(name)
    except Exception as exc:
        if not _is_not_found(exc):
            raise SnapshotUnavailableError(
                "Snapshot lookup failed; verify access before building.", state="lookup_failed"
            ) from None
    else:
        if existing is not None:
            raise SnapshotUnavailableError(
                "Snapshot already exists; verify or activate it instead of replacing it.", state="exists"
            )
    client.snapshot.create(
        CreateSnapshotParams(name=name, image=DEFAULT_DEFINITION.image(), resources=DEFAULT_DEFINITION.resources()),
        timeout=timeout_seconds,
        on_logs=on_logs,
    )
    return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)


def activate_snapshot(
    *,
    name: str = SNAPSHOT_NAME,
    api_key: str | None = None,
    org_id: str | None = None,
    timeout_seconds: float = SNAPSHOT_BUILD_TIMEOUT_SECONDS,
) -> SnapshotIdentity:
    """Activate explicitly, then poll readiness without repeating the mutation."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Snapshot activation timeout_seconds must be finite and positive")
    key, organization = _resolved_credentials(api_key, org_id)
    client = Daytona(DaytonaConfig(api_key=key, organization_id=organization))
    try:
        return preflight(client.snapshot, name=name, api_key=key, org_id=organization, refresh=True)
    except SnapshotUnavailableError as exc:
        if exc.state != "inactive":
            raise
    deadline = time.monotonic() + timeout_seconds
    # Daytona 0.224.0's public activate() omits the generated client's request timeout.
    snapshots_api = cast(Any, client.snapshot)._SnapshotService__snapshots_api
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SnapshotUnavailableError(
            "Activation is not confirmed; verify provider state before retrying.", state="pending"
        ) from None
    snapshot = snapshots_api.get_snapshot(name, _request_timeout=remaining)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SnapshotUnavailableError(
            "Activation is not confirmed; verify provider state before retrying.", state="pending"
        ) from None
    snapshots_api.activate_snapshot(snapshot.id, _request_timeout=remaining)

    def get_snapshot_with_timeout(snapshot_name: str) -> Any:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError("Snapshot activation readiness deadline expired")
        return snapshots_api.get_snapshot(snapshot_name, _request_timeout=remaining_seconds)

    timed_snapshot_service = SimpleNamespace(get=get_snapshot_with_timeout)
    while True:
        try:
            return preflight(timed_snapshot_service, name=name, api_key=key, org_id=organization, refresh=True)
        except SnapshotUnavailableError as exc:
            if exc.state not in {"inactive", "pending", "building", "pulling", "snapshotting", "activating"}:
                if exc.state == "lookup_failed" and time.monotonic() >= deadline:
                    raise SnapshotUnavailableError(
                        "Activation is not confirmed; verify provider state before retrying.", state="pending"
                    ) from None
                raise
            if time.monotonic() >= deadline:
                raise SnapshotUnavailableError(
                    "Activation is not confirmed; verify provider state before retrying.", state="pending"
                ) from None
        time.sleep(min(2, max(0, deadline - time.monotonic())))
