"""Create or inspect Fleet's immutable Daytona Snapshot.

This operator command never runs during API startup.  It accepts no credentials
as arguments; the normal ``FLEET_DAYTONA_API_KEY`` setting is resolved only
after argparse handles ``--help``.
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from daytona import CreateSnapshotParams, Resources

from fleet_rlm.daytona.client import build_daytona_client
from fleet_rlm.daytona.errors import is_sandbox_not_found, sanitize_provider_message
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec, build_snapshot_image


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the immutable Fleet Daytona Snapshot.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "check"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--name", required=True, help="Immutable snapshot name, for example fleet-rlm-python313-v2")
    return parser


def _spec(name: str) -> DaytonaSandboxSpec:
    return DaytonaSandboxSpec(snapshot=name)


def _validate_snapshot(snapshot: Any, spec: DaytonaSandboxSpec) -> None:
    state = str(getattr(getattr(snapshot, "state", None), "value", getattr(snapshot, "state", ""))).lower()
    if state != "active":
        raise RuntimeError("snapshot is not active")
    if str(getattr(snapshot, "name", "")) != spec.snapshot:
        raise RuntimeError("snapshot name did not match the requested immutable name")
    build_info = getattr(snapshot, "build_info", None)
    actual_dockerfile = getattr(build_info, "dockerfile_content", None)
    expected_dockerfile = build_snapshot_image(spec).dockerfile()
    if not isinstance(actual_dockerfile, str) or actual_dockerfile != expected_dockerfile:
        raise RuntimeError("snapshot image metadata did not match the Fleet contract")
    expected = (("cpu", spec.cpu), ("mem", spec.memory_gib), ("disk", spec.disk_gib))
    for attribute, value in expected:
        if int(getattr(snapshot, attribute, 0) or 0) != value:
            raise RuntimeError("snapshot resources did not match the Fleet contract")


def _get_existing(client: Any, name: str) -> Any | None:
    try:
        return client.snapshot.get(name)
    except Exception as exc:  # provider SDK has a dedicated not-found family
        if is_sandbox_not_found(exc):
            return None
        raise RuntimeError(sanitize_provider_message(str(exc))) from exc


def create_snapshot(client: Any, spec: DaytonaSandboxSpec) -> None:
    existing = _get_existing(client, spec.snapshot)
    if existing is not None:
        _validate_snapshot(existing, spec)
        print(f"Snapshot {spec.snapshot} already exists and matches its public contract.")
        return

    def on_logs(_chunk: str) -> None:
        # Provider logs can contain build paths; keep output useful but closed.
        print("Snapshot build progress received.")

    try:
        snapshot = client.snapshot.create(
            CreateSnapshotParams(
                name=spec.snapshot,
                image=build_snapshot_image(spec),
                resources=Resources(cpu=spec.cpu, memory=spec.memory_gib, disk=spec.disk_gib),
            ),
            on_logs=on_logs,
        )
        _validate_snapshot(snapshot, spec)
    except Exception as exc:  # do not expose provider messages or build output
        raise RuntimeError("Daytona snapshot creation failed safely") from exc
    print(f"Snapshot {spec.snapshot} is active and matches the Fleet resource contract.")


def check_snapshot(client: Any, spec: DaytonaSandboxSpec) -> None:
    snapshot = _get_existing(client, spec.snapshot)
    if snapshot is None:
        raise RuntimeError("configured Daytona snapshot was not found")
    _validate_snapshot(snapshot, spec)
    print(f"Snapshot {spec.snapshot} is active and matches the Fleet resource contract.")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    spec = _spec(args.name)
    from fleet_rlm.config import load_runtime_settings

    # Snapshot operations still use the selected policy profile for credentials
    # and dotenv loading, but do not require the profile's runtime to be live.
    settings = load_runtime_settings()
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        raise SystemExit("FLEET_DAYTONA_API_KEY is required")
    client = build_daytona_client(settings)
    if args.command == "create":
        create_snapshot(client, spec)
    else:
        check_snapshot(client, spec)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
