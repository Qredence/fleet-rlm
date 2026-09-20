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
            "import pathlib, tomllib; "
            "root=pathlib.Path('.codex'); "
            "[tomllib.loads(p.read_text()) for p in root.rglob('*.toml')]; "
            "print('codex-config-ok')",
        ],
    ),
    (
        "codex-hooks-syntax",
        [
            "zsh",
            "-n",
            ".codex/workspace-bootstrap.zsh",
            ".codex/maintenance.zsh",
            ".codex/cloud-preflight.zsh",
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
        with urllib.request.urlopen(url, timeout=timeout) as response:
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
        "--output",
        type=Path,
        default=Path("artifacts/codex-feedback-loop/report.json"),
        help="Path for emitted JSON report.",
    )
    parser.add_argument(
        "--app-url",
        default="http://127.0.0.1:8000/api/v1/health",
        help="Local application health endpoint probed when --profile app is selected.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run commands and write the report."""
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    command_results = [run_command(name, command, repo_root) for name, command in SAFE_COMMANDS]
    probe_result: HttpProbe | None = None
    if args.profile == "app":
        probe_result = probe_url(args.app_url)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": args.profile,
        "commands": [asdict(result) for result in command_results],
        "app_probe": asdict(probe_result) if probe_result else None,
        "ok": all(result.returncode == 0 for result in command_results) and (probe_result is None or probe_result.ok),
    }
    write_report(report, args.output)
    failed = [result.name for result in command_results if result.returncode != 0]
    if probe_result and not probe_result.ok:
        failed.append("app-probe")
    if failed:
        sys.stderr.write(f"codex feedback loop reported failures: {', '.join(failed)}\n")
        return 1
    sys.stdout.write(f"codex feedback loop passed ({len(command_results)} commands checked)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
