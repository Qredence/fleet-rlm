"""Create or inspect Fleet's immutable Daytona Snapshot.

This operator command never runs during API startup.  It accepts no credentials
as arguments; the normal ``FLEET_DAYTONA_API_KEY`` setting is resolved only
after argparse handles ``--help``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from daytona import CreateSnapshotParams, Resources

from fleet_rlm.daytona.errors import is_sandbox_not_found, sanitize_provider_message
from fleet_rlm.daytona.lifecycle import confirm_absence
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
from fleet_rlm.daytona.provisioning import (
    DEFAULT_CHILD_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_NAME,
    SEMANTIC_CHILD_RESOURCES,
    SESSION_RESOURCES,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    build_snapshot_image,
    environment_manifest,
)


def _parser() -> argparse.ArgumentParser:
    """
    Create the command-line parser for snapshot creation and inspection commands.

    Returns:
        argparse.ArgumentParser: Parser with required `create` and `check` subcommands and snapshot name arguments.
    """
    parser = argparse.ArgumentParser(description="Manage the immutable Fleet Daytona Snapshot.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "create", "check", "verify-runtime"):
        sub = subcommands.add_parser(command)
        sub.add_argument(
            "--profile",
            choices=tuple(profile.value for profile in DaytonaEnvironmentProfile),
            default=DaytonaEnvironmentProfile.SESSION.value,
            help="Immutable environment profile to reconcile",
        )
        sub.add_argument(
            "--name",
            required=True,
            help=(f"Immutable snapshot name, for example {DEFAULT_SNAPSHOT_NAME} or {DEFAULT_CHILD_SNAPSHOT_NAME}"),
        )
    return parser


def _spec(name: str, profile: str = DaytonaEnvironmentProfile.SESSION.value) -> DaytonaSandboxSpec:
    selected = DaytonaEnvironmentProfile(profile)
    resources = SEMANTIC_CHILD_RESOURCES if selected is DaytonaEnvironmentProfile.SEMANTIC_CHILD else SESSION_RESOURCES
    return DaytonaSandboxSpec(
        snapshot=name,
        cpu=resources[0],
        memory_gib=resources[1],
        disk_gib=resources[2],
        profile=selected,
    )


def plan_snapshot(spec: DaytonaSandboxSpec) -> None:
    """Print a non-secret immutable image plan without contacting Daytona."""
    manifest = environment_manifest(spec)
    print(
        json.dumps(
            {
                "profile": spec.profile.value,
                "snapshot": spec.snapshot,
                "resources": {"cpu": spec.cpu, "memory_gib": spec.memory_gib, "disk_gib": spec.disk_gib},
                "manifest_sha256": manifest.digest,
                "dependency_sha256": manifest.dependency_sha256,
                "volume_allowed": manifest.volume_allowed,
                "warm_pool_eligible": manifest.warm_pool_eligible,
            },
            sort_keys=True,
        )
    )


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


async def _get_existing(client: Any, name: str) -> Any | None:
    try:
        return await client.snapshot.get(name)
    except Exception as exc:  # provider SDK has a dedicated not-found family
        if is_sandbox_not_found(exc):
            return None
        raise RuntimeError(sanitize_provider_message(str(exc))) from exc


async def create_snapshot(client: Any, spec: DaytonaSandboxSpec) -> None:
    existing = await _get_existing(client, spec.snapshot)
    if existing is not None:
        _validate_snapshot(existing, spec)
        print(f"Snapshot {spec.snapshot} already exists and matches its public contract.")
        return

    def on_logs(_chunk: str) -> None:
        # Provider logs can contain build paths; keep output useful but closed.
        print("Snapshot build progress received.")

    try:
        snapshot = await client.snapshot.create(
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


async def check_snapshot(client: Any, spec: DaytonaSandboxSpec) -> None:
    snapshot = await _get_existing(client, spec.snapshot)
    if snapshot is None:
        raise RuntimeError("configured Daytona snapshot was not found")
    _validate_snapshot(snapshot, spec)
    print(f"Snapshot {spec.snapshot} is active and matches the Fleet resource contract.")


async def verify_runtime(client: Any, spec: DaytonaSandboxSpec) -> None:
    """Run a disposable no-Volume runtime probe for one immutable profile."""
    platform = LiveDaytonaPlatform(client, spec)
    sandbox: Any | None = None
    try:
        sandbox = await platform.create(
            profile=spec.profile,
            with_volume=False,
            labels={"fleet.runtime": "snapshot-verification", "fleet.profile": spec.profile.value},
            ephemeral=True,
        )
        context = await sandbox.code_interpreter.create_context()
        try:
            manifest = environment_manifest(spec)
            expected = json.dumps(manifest.as_dict(), sort_keys=True, separators=(",", ":"))
            code = (
                "import getpass, hashlib, json, pathlib, shutil, sys\n"
                "manifest_path = pathlib.Path('/opt/fleet/runtime-manifest.json')\n"
                "manifest = json.loads(manifest_path.read_text())\n"
                f"expected = json.loads({expected!r})\n"
                "assert manifest == expected\n"
                "assert hashlib.sha256("
                "json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()"
                ").hexdigest() "
                "== "
                f"{manifest.digest!r}\n"
                "assert sys.version_info[:3] == (3, 13, 13)\n"
                "assert getpass.getuser() == 'daytona'\n"
                "assert pathlib.Path.cwd() == pathlib.Path('/home/daytona')\n"
                "assert shutil.which('git')\n"
                "print('fleet-snapshot-runtime-ok')"
            )
            result = await sandbox.code_interpreter.run_code(code, context=context)
            stdout = getattr(result, "stdout", result)
            if getattr(result, "error", None) or str(stdout or "").strip() != "fleet-snapshot-runtime-ok":
                raise RuntimeError("snapshot runtime probe failed")
        finally:
            await sandbox.code_interpreter.delete_context(context)
    finally:
        if sandbox is not None:
            await platform.delete(sandbox)
            sandbox_id = str(getattr(sandbox, "id", ""))
            confirmation = await confirm_absence(
                probe=platform.get,
                sandbox_id=sandbox_id,
                timeout_s=120.0,
                poll_interval_s=1.0,
            )
            if not confirmation.absent:
                raise RuntimeError("snapshot verification sandbox was not deleted")
    print(f"Snapshot {spec.snapshot} runtime probe passed and disposable sandbox was deleted.")


async def _run(args: argparse.Namespace) -> int:
    spec = _spec(args.name, args.profile)
    if args.command == "plan":
        plan_snapshot(spec)
        return 0
    from fleet_rlm.config.loader import load_runtime_settings

    # Snapshot operations still use the selected policy profile for credentials
    # and dotenv loading, but do not require the profile's runtime to be live.
    settings = load_runtime_settings()
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        raise SystemExit("FLEET_DAYTONA_API_KEY is required")
    client = build_daytona_client(settings)
    try:
        if args.command == "create":
            await create_snapshot(client, spec)
        elif args.command == "check":
            await check_snapshot(client, spec)
        else:
            await verify_runtime(client, spec)
    finally:
        await client.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except SystemExit:
        raise
    except BaseException:
        # Snapshot/provider failures are operator-visible only as a stable
        # category.  Do not print SDK traces, build commands, or credentials.
        print("Daytona snapshot operation failed safely.", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
