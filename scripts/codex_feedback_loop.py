#!/usr/bin/env python3
"""Run the local Codex feedback loop and emit a concise report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SAFE_COMMANDS = (
    (
        "codex-config",
        [
            sys.executable,
            "-c",
            "import json, pathlib, tomllib; "
            "root=pathlib.Path('.codex'); "
            "[tomllib.loads(p.read_text()) for p in root.rglob('*.toml')]; "
            "json.loads((root/'hooks.json').read_text()); "
            "print('codex-config-ok')",
        ],
    ),
    (
        "codex-hooks-syntax",
        [
            "zsh",
            "-n",
            ".codex/workspace-bootstrap.zsh",
            ".codex/hooks/block-env-edit.zsh",
            ".codex/hooks/generated-artifact-check.zsh",
            ".codex/hooks/python-format.zsh",
        ],
    ),
    ("harness", [sys.executable, "scripts/check_harness_engineering.py"]),
    ("agents-freshness", [sys.executable, "scripts/check_agents_md_freshness.py"]),
    ("docs-quality", [sys.executable, "scripts/check_docs_quality.py"]),
    ("format-check", ["make", "format-check"]),
)


@dataclass(frozen=True)
class CommandResult:
    """A single command execution result."""

    name: str
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class HttpProbe:
    """A lightweight HTTP probe result."""

    url: str
    ok: bool
    status: int | None
    body_tail: str
    error: str | None = None


def run_command(name: str, command: list[str], repo_root: Path) -> CommandResult:
    """Run a command and keep bounded output for the report."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return CommandResult(
            name=name,
            command=command,
            returncode=124,
            duration_seconds=round(time.monotonic() - start, 2),
            stdout_tail=tail(stdout),
            stderr_tail=tail(f"command timed out after {exc.timeout} seconds"),
        )
    except OSError as exc:
        return CommandResult(
            name=name,
            command=command,
            returncode=127,
            duration_seconds=round(time.monotonic() - start, 2),
            stdout_tail="",
            stderr_tail=tail(str(exc)),
        )
    return CommandResult(
        name=name,
        command=command,
        returncode=result.returncode,
        duration_seconds=round(time.monotonic() - start, 2),
        stdout_tail=tail(result.stdout),
        stderr_tail=tail(result.stderr),
    )


def probe_url(url: str, timeout: float = 5.0) -> HttpProbe:
    """Probe a local URL without requiring third-party dependencies."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = response.read(4096).decode("utf-8", errors="replace")
            status = response.status
            return HttpProbe(url=url, ok=200 <= status < 400, status=status, body_tail=tail(body))
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace")
        return HttpProbe(url=url, ok=False, status=exc.code, body_tail=tail(body), error=str(exc))
    except OSError as exc:
        return HttpProbe(url=url, ok=False, status=None, body_tail="", error=str(exc))


def tail(value: str, max_chars: int = 1600) -> str:
    """Return a bounded tail string."""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def write_report(report: dict[str, Any], output: Path) -> None:
    """Write the JSON report."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("safe", "app"),
        default="safe",
        help="safe runs static/local checks; app also probes a running local API.",
    )
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:8000",
        help="Local API server URL for the app profile.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/codex-feedback-loop/report.json"),
        help="Report path.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    command_results = [run_command(name, command, repo_root) for name, command in SAFE_COMMANDS]
    probes: list[HttpProbe] = []
    if args.profile == "app":
        base = args.server_url.rstrip("/")
        probes = [
            probe_url(f"{base}/health"),
            probe_url(f"{base}/api/v1/runtime/status"),
        ]

    report = {
        "profile": args.profile,
        "commands": [asdict(result) for result in command_results],
        "http_probes": [asdict(probe) for probe in probes],
    }
    output_path = repo_root / args.output
    write_report(report, output_path)

    failed_commands = [result for result in command_results if result.returncode != 0]
    failed_probes = [probe for probe in probes if not probe.ok]
    print(f"Codex feedback loop report: {output_path}")
    if failed_commands or failed_probes:
        for result in failed_commands:
            print(f"FAIL command {result.name}: exit {result.returncode}", file=sys.stderr)
        for probe in failed_probes:
            print(f"FAIL probe {probe.url}: {probe.error or probe.status}", file=sys.stderr)
        return 1
    print("OK: Codex feedback loop completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
