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
from textwrap import dedent
from typing import Any

from daytona import CreateSnapshotParams, Resources

from fleet_rlm.daytona.diagnostics import (
    build_snapshot_image,
    environment_manifest,
    snapshot_dependency_import_names,
)
from fleet_rlm.daytona.errors import is_sandbox_not_found, sanitize_provider_message
from fleet_rlm.daytona.leases import confirm_absence
from fleet_rlm.daytona.provider import LiveDaytonaPlatform, build_daytona_client
from fleet_rlm.daytona.specs import (
    DEFAULT_CHILD_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_NAME,
    SEMANTIC_CHILD_RESOURCES,
    SESSION_RESOURCES,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
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


def _browser_driver_probe_code() -> str:
    """Operator-only local fixture: real driver interaction and owned cleanup."""
    return dedent("""
        import http.server, os, runpy, subprocess, threading, uuid
        version = subprocess.run(['agent-browser', '--version'], check=True, capture_output=True, text=True, timeout=10)
        assert version.stdout.strip() == 'agent-browser ' + expected['agent_browser']['version']
        core = subprocess.run(['agent-browser', 'skills', 'get', 'core'], check=True, capture_output=True, timeout=10)
        assert b'agent-browser' in core.stdout
        owner = runpy.run_path('/opt/fleet/browser_session.py')
        class Fixture(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                pages = {
                    '/': (
                        b'<body><button id="expand">Expand</button><p id="expanded"></p>'
                        b'<p id="delayed"></p><a href="/page2">Next page</a>'
                        b'<iframe id="frame" src="/frame"></iframe><script>'
                        b'document.getElementById("expand").onclick=function(){'
                        b'document.getElementById("expanded").innerText="Fleet expanded evidence"};'
                        b'setTimeout(function(){document.getElementById("delayed").innerText='
                        b'"Fleet delayed evidence"},100)</script></body>'
                    ),
                    '/page2': b'<body>Fleet second page evidence</body>',
                    '/frame': b'<body>Fleet frame evidence</body>',
                }
                self.wfile.write(pages.get(self.path, b'<body></body>'))
            def log_message(self, *_args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for run_index in range(2):
                scratch = pathlib.Path('/tmp/fleet') / str(uuid.uuid4())
                scratch.mkdir(parents=True)
                groups = {}
                try:
                    binding = owner['start'](str(scratch))
                    groups = {int(pid): started for pid, started in binding['process_groups'].items()}
                    prefix = binding['command']
                    environment = {**os.environ, 'FLEET_BROWSER_RUN': scratch.name}
                    def driver(*arguments):
                        result = subprocess.run(
                            prefix + list(arguments), env=environment, check=True,
                            capture_output=True, text=True, timeout=30
                        )
                        return result.stdout
                    driver('open', 'http://127.0.0.1:' + str(server.server_port))
                    assert 'fleet_run=one' not in driver('eval', 'document.cookie')
                    if run_index == 0:
                        driver('eval', "document.cookie='fleet_run=one'; 'set'")
                        assert 'fleet_run=one' in driver('eval', 'document.cookie')
                    driver('wait', '--text', 'Fleet delayed evidence')
                    assert 'Fleet delayed evidence' in driver('get', 'text', 'body')
                    assert 'Expand' in driver('snapshot', '-i', '-c')
                    driver('find', 'role', 'button', 'click', '--name', 'Expand')
                    assert 'Fleet expanded evidence' in driver('get', 'text', 'body')
                    driver('frame', '#frame')
                    driver('wait', '--text', 'Fleet frame evidence')
                    assert 'Fleet frame evidence' in driver('get', 'text', 'body')
                    driver('frame', 'main')
                    driver('find', 'role', 'link', 'click', '--name', 'Next page')
                    driver('wait', '--url', '**/page2')
                    assert 'Fleet second page evidence' in driver('get', 'text', 'body')
                    for pid in owner['_owned_processes'](scratch.name):
                        arguments = pathlib.Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\\0')
                        if any(item.startswith(b'--user-data-dir=') for item in arguments):
                            assert b'--no-sandbox' not in arguments
                finally:
                    owner['close'](str(scratch), groups=groups)
                    assert not owner['_owned_processes'](scratch.name)
                    assert not owner['_groups_alive'](groups)
                    shutil.rmtree(scratch)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    """)


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
            expected = json.dumps(manifest.image_identity(), sort_keys=True, separators=(",", ":"))
            compatible_profiles = tuple(profile.value for profile in manifest.compatible_profiles)
            code = (
                "import getpass, hashlib, importlib, importlib.metadata, json, pathlib, shutil, sys, sysconfig\n"
                "manifest_path = pathlib.Path('/opt/fleet/runtime-manifest.json')\n"
                "manifest = json.loads(manifest_path.read_text())\n"
                f"expected = json.loads({expected!r})\n"
                f"assert manifest.get('profile') in {compatible_profiles!r}\n"
                "image_identity = dict(manifest)\n"
                "image_identity.pop('profile', None)\n"
                "assert image_identity == expected\n"
                "assert hashlib.sha256("
                "json.dumps(image_identity, sort_keys=True, separators=(',', ':')).encode()"
                ").hexdigest() "
                "== "
                f"{manifest.digest!r}\n"
                "assert sys.version_info[:3] == (3, 13, 13)\n"
                "assert pathlib.Path(sys.executable).is_file()\n"
                f"dependencies = {snapshot_dependency_import_names(spec=spec)!r}\n"
                "for distribution, module, version in dependencies:\n"
                "    imported = importlib.import_module(module)\n"
                "    assert importlib.metadata.version(distribution) == version\n"
                "    assert pathlib.Path(imported.__file__).resolve().is_relative_to("
                "pathlib.Path(sysconfig.get_paths()['purelib']).resolve())\n"
                "assert getpass.getuser() == 'daytona'\n"
                "assert pathlib.Path.cwd() == pathlib.Path('/home/daytona')\n"
                "assert shutil.which('git')\n"
            )
            if manifest.browser_enabled:
                code += (
                    "from playwright.sync_api import sync_playwright\n"
                    "with sync_playwright() as playwright:\n"
                    "    browser = playwright.chromium.launch(headless=True, chromium_sandbox=True, timeout=15000)\n"
                    "    try:\n"
                    "        browser_context = browser.new_context(service_workers='block', accept_downloads=False)\n"
                    "        try:\n"
                    "            page = browser_context.new_page()\n"
                    "            page.set_content('<title>Fleet probe</title><body>before<script>"
                    'document.body.textContent="fleet-browser-render-ok"</script></body>\', timeout=10000)\n'
                    "            assert page.title() == 'Fleet probe'\n"
                    "            assert page.locator('body').inner_text(timeout=10000) == 'fleet-browser-render-ok'\n"
                    "        finally:\n"
                    "            browser_context.close()\n"
                    "    finally:\n"
                    "        browser.close()\n"
                )
            if manifest.agent_browser_enabled:
                code += _browser_driver_probe_code()
            code += "print('fleet-snapshot-runtime-ok')"
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

    # Snapshot operations still use the single configuration for credentials
    # and dotenv loading, but do not require live runtime admission.
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
    except Exception:
        # Snapshot/provider failures are operator-visible only as a stable
        # category.  Do not print SDK traces, build commands, or credentials.
        print("Daytona snapshot operation failed safely.", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
