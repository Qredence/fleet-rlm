"""Run a disposable Daytona proof of Phase 5 sandbox capabilities.

This records observed behavior, including the operator's explicit waiver of
the public-only network-policy parity gate for URL-subsystem deletion.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import textwrap
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.daytona.runtime import (
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    build_daytona_client,
    sandbox_spec_from_settings,
    volume_config_from_settings,
)
from fleet_rlm.sessions.bindings import workspace_volume_subpath

_ROOT = Path(__file__).resolve().parents[1]
_SANDBOX_CODE = textwrap.dedent(
    """
    import hashlib, importlib, json, os, pathlib, site, socket, subprocess, sys, urllib.request
    from datetime import datetime, timezone

    root = pathlib.Path('/workspace')
    root.mkdir(exist_ok=True)
    print('STAGE:download', flush=True)
    sources = root / 'sources'
    sources.mkdir(exist_ok=True)
    records = []
    for index, url in enumerate(('https://example.com/', 'https://www.python.org/')):
        target = sources / f'phase5-{index}.html'
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(url, timeout=10) as response, target.open('wb') as output:
            while chunk := response.read(65536):
                total += len(chunk)
                if total > 2_000_000:
                    raise ValueError('download limit exceeded')
                digest.update(chunk)
                output.write(chunk)
        records.append({'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                        'path': str(target), 'sha256': digest.hexdigest(), 'bytes': total})
    (sources / 'phase5-provenance.json').write_text(json.dumps(records, indent=2))
    legacy = sources / 'urls' / (hashlib.sha256(records[0]['url'].encode()).hexdigest() + '.txt')
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_bytes(pathlib.Path(records[0]['path']).read_bytes())

    def command(*args, timeout=60):
        completed = subprocess.run(args, cwd=root, text=True, capture_output=True, timeout=timeout)
        if completed.returncode:
            raise RuntimeError(f'command failed: {args[0]}')
        return completed.stdout[:2000]

    print('STAGE:repository', flush=True)
    repository = pathlib.Path('/tmp/phase5-repository')
    command('git', 'clone', '--depth=1', 'https://github.com/pallets/markupsafe.git', str(repository), timeout=90)
    commit = subprocess.run(('git', '-C', str(repository), 'rev-parse', 'HEAD'), text=True,
                            capture_output=True, check=True, timeout=10).stdout.strip()
    (root / 'phase5-repository-commit.txt').write_text(commit + '\\n')
    command('git', '-C', str(repository), 'checkout', '--detach', commit, timeout=10)
    inventory = subprocess.run(('git', '-C', str(repository), 'ls-files'), text=True,
                               capture_output=True, check=True, timeout=10).stdout.splitlines()
    selected = next(path for path in inventory if path.endswith('README.md'))
    excerpt = (repository / selected).read_text(errors='replace').splitlines()[:3]
    hits = subprocess.run(('git', '-C', str(repository), 'grep', '-n', '-i', 'MarkupSafe', '--', selected),
                          text=True, capture_output=True, check=True, timeout=10).stdout.splitlines()
    selected_python = next(path for path in inventory if path.endswith('.py'))
    command(sys.executable, '-m', 'py_compile', str(repository / selected_python), timeout=20)
    print('STAGE:dependency', flush=True)
    command(sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
            'roman==5.1', 'ddgs==9.16.0', timeout=120)
    site.addsitedir(site.getusersitepackages())
    importlib.invalidate_caches()
    imported = importlib.import_module('roman')
    print('STAGE:search', flush=True)
    DDGS = importlib.import_module('ddgs').DDGS
    search_results = DDGS(timeout=10).text('Python programming documentation', max_results=3)
    manifest = subprocess.run((sys.executable, '-m', 'pip', 'freeze'), text=True,
                              capture_output=True, check=True, timeout=20).stdout
    (root / 'phase5-dependencies.txt').write_text(manifest)
    print('STAGE:network', flush=True)
    network = {}
    for name, host, port in [('metadata', '169.254.169.254', 80),
                             ('private', '10.255.255.1', 80), ('public', '1.1.1.1', 443)]:
        try:
            with socket.create_connection((host, port), timeout=2):
                network[name] = 'connected'
        except OSError as exc:
            network[name] = type(exc).__name__
    network['dns_loopback_alias'] = socket.gethostbyname('127.0.0.1.nip.io')
    network['dns_private_alias'] = socket.gethostbyname('10.255.255.1.nip.io')
    try:
        with urllib.request.urlopen(
            'https://httpbin.org/redirect-to?url=http%3A%2F%2F169.254.169.254%2F', timeout=5
        ) as response:
            network['metadata_redirect'] = response.status
    except Exception as exc:
        network['metadata_redirect'] = type(exc).__name__
    print(json.dumps({'sources': records, 'repository_commit': commit,
                      'repository_file_count': len(inventory), 'selected_path': selected,
                      'selected_preview': [{'line': index + 1, 'text': line[:160]}
                                           for index, line in enumerate(excerpt)],
                      'text_search_hits': len(hits), 'source_check_passed': True,
                      'import_verified': hasattr(imported, 'toRoman'),
                      'search_result_count': len(search_results),
                      'active_interpreter': sys.executable,
                      'manifest_has_package': 'roman==5.1' in manifest,
                      'manifest_path': str(root / 'phase5-dependencies.txt'),
                      'network': network,
                      'host_secrets_present': any(name in os.environ for name in
                          ('FLEET_DAYTONA_API_KEY', 'FLEET_DATABASE_URL', 'BRAVE_API_KEY', 'TAVILY_API_KEY',
                           'ALIBABA_API_KEY', 'DATABRICKS_TOKEN', 'POSTHOG_PROJECT_TOKEN'))}))
    """
)


async def _run(output: Path) -> None:
    load_dotenv(_ROOT / ".env", override=False)
    if os.environ.get("FLEET_LIVE", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("FLEET_LIVE=1 is required")
    settings = load_runtime_settings()
    if settings.daytona_api_key is None:
        raise RuntimeError("configured Daytona credential is required")
    client = build_daytona_client(settings)
    platform = LiveDaytonaPlatform(client, sandbox_spec_from_settings(settings))
    volume_config = volume_config_from_settings(settings)
    sandbox: Any | None = None
    replacement: Any | None = None
    receipt: dict[str, Any] = {"schema": "fleet.phase5-sandbox-proof/v1", "sandbox_absent": False}
    try:
        volume = await LiveDaytonaVolumeClient(client).get(volume_config.name, create=False)
        volume_id = str(volume.id)
        volume_subpath = workspace_volume_subpath(uuid4())
        mount = {
            "with_volume": True,
            "volume_id": volume_id,
            "mount_path": "/workspace",
            "volume_subpath": volume_subpath,
            "ephemeral": True,
        }
        sandbox = await platform.create(**mount)
        receipt["provider_network_policy"] = {
            "block_all": getattr(sandbox, "network_block_all", None),
            "allow_list": getattr(sandbox, "network_allow_list", None),
            "domain_allow_list": getattr(sandbox, "domain_allow_list", None),
        }
        result = await sandbox.process.code_run(_SANDBOX_CODE, timeout=240)
        if getattr(result, "exit_code", 1) != 0:
            receipt["capabilities_passed"] = False
            receipt["failure_kind"] = "sandbox_code_failed"
            sandbox_output = str(getattr(result, "result", ""))
            stages = [part.splitlines()[0] for part in sandbox_output.split("STAGE:")[1:]]
            receipt["failure_stage"] = stages[-1] if stages else "unknown"
            receipt["failure_category"] = next(
                (
                    category
                    for category in (
                        "PermissionError",
                        "FileNotFoundError",
                        "ModuleNotFoundError",
                        "TimeoutExpired",
                        "RuntimeError",
                        "SyntaxError",
                    )
                    if category in sandbox_output
                ),
                "unclassified",
            )
        else:
            payload = json.loads(str(getattr(result, "result", "")).splitlines()[-1])
            receipt["capabilities"] = payload
            receipt["capabilities_passed"] = bool(
                len(payload["sources"]) == 2
                and payload["repository_commit"]
                and payload["text_search_hits"] > 0
                and payload["source_check_passed"]
                and payload["import_verified"]
                and payload["search_result_count"] > 0
                and payload["manifest_has_package"]
                and not payload["host_secrets_present"]
            )
        # A connection timeout to an arbitrary private address proves only
        # that no service responded. The operator explicitly waived the
        # public-only network-policy parity requirement for URL deletion.
        receipt["network_policy_parity_proven"] = False
        receipt["network_policy_waived_by_operator"] = True
        if receipt.get("capabilities_passed"):
            await platform.stop(str(sandbox.id), timeout=60, force=True)
            await platform.delete(sandbox)
            receipt["first_sandbox_absent"] = await platform.get(str(sandbox.id)) is None
            sandbox = None
            replacement = await platform.create(**mount)
            check_code = textwrap.dedent(
                """
                import hashlib, json, pathlib, subprocess, sys
                root = pathlib.Path('/workspace')
                records = json.loads((root / 'sources/phase5-provenance.json').read_text())
                verified = all(hashlib.sha256(pathlib.Path(item['path']).read_bytes()).hexdigest() == item['sha256']
                               for item in records)
                legacy = root / 'sources' / 'urls' / (hashlib.sha256(records[0]['url'].encode()).hexdigest() + '.txt')
                legacy_readable = legacy.read_bytes() == pathlib.Path(records[0]['path']).read_bytes()
                manifest = (root / 'phase5-dependencies.txt').read_text()
                commit = (root / 'phase5-repository-commit.txt').read_text().strip()
                package = next(line for line in manifest.splitlines() if line.lower().startswith('roman=='))
                subprocess.run((sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', package),
                               capture_output=True, check=True, timeout=90)
                subprocess.run((sys.executable, '-c', 'import roman'), capture_output=True, check=True, timeout=10)
                print(json.dumps({'sources_verified': verified, 'legacy_source_readable': legacy_readable,
                                  'source_count': len(records),
                                  'manifest_has_package': 'roman==5.1' in manifest,
                                  'dependency_reconstructed': True, 'commit_length': len(commit)}))
                """
            )
            check = await replacement.process.code_run(check_code, timeout=30)
            if getattr(check, "exit_code", 1) == 0:
                receipt["workspace_replacement"] = json.loads(str(getattr(check, "result", "")).splitlines()[0])
                receipt["workspace_replacement_passed"] = all(
                    (
                        receipt["workspace_replacement"]["sources_verified"],
                        receipt["workspace_replacement"]["legacy_source_readable"],
                        receipt["workspace_replacement"]["source_count"] == 2,
                        receipt["workspace_replacement"]["manifest_has_package"],
                        receipt["workspace_replacement"]["dependency_reconstructed"],
                        receipt["workspace_replacement"]["commit_length"] == 40,
                    )
                )
            else:
                receipt["workspace_replacement_passed"] = False
                replacement_output = str(getattr(check, "result", ""))
                receipt["replacement_failure_category"] = next(
                    (
                        category
                        for category in (
                            "FileNotFoundError",
                            "PermissionError",
                            "JSONDecodeError",
                            "TimeoutError",
                            "RuntimeError",
                        )
                        if category in replacement_output
                    ),
                    "unclassified",
                )
    finally:
        cleanup_sandbox = replacement or sandbox
        if cleanup_sandbox is not None:
            with contextlib.suppress(Exception):
                await cleanup_sandbox.process.code_run(
                    "import pathlib, shutil; root=pathlib.Path('/workspace'); "
                    "shutil.rmtree(root / 'sources', ignore_errors=True); "
                    "(root / 'phase5-repository-commit.txt').unlink(missing_ok=True); "
                    "(root / 'phase5-dependencies.txt').unlink(missing_ok=True)",
                    timeout=30,
                )
        if replacement is not None:
            with contextlib.suppress(Exception):
                await platform.stop(str(replacement.id), timeout=60, force=True)
            with contextlib.suppress(Exception):
                await platform.delete(replacement)
            receipt["replacement_sandbox_absent"] = await platform.get(str(replacement.id)) is None
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await platform.stop(str(sandbox.id), timeout=60, force=True)
            with contextlib.suppress(Exception):
                await platform.delete(sandbox)
            receipt["sandbox_absent"] = await platform.get(str(sandbox.id)) is None
        else:
            receipt["sandbox_absent"] = receipt.get("first_sandbox_absent", False)
        close = getattr(client, "close", None)
        if close is not None:
            await close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if not all(
        (
            receipt.get("capabilities_passed"),
            receipt.get("workspace_replacement_passed"),
            receipt["sandbox_absent"],
            receipt.get("replacement_sandbox_absent"),
        )
    ):
        raise RuntimeError("Phase 5 live capability proof failed; inspect the receipt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.output))


if __name__ == "__main__":
    main()
