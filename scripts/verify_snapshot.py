#!/usr/bin/env python3
"""Verify a Daytona snapshot by creating a temporary sandbox."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from fleet_rlm.integrations.daytona.config import resolve_daytona_config
from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime
from fleet_rlm.integrations.daytona.snapshots import DEFAULT_SNAPSHOT_NAME


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT_NAME,
        help=f"Snapshot name to verify. Defaults to {DEFAULT_SNAPSHOT_NAME!r}.",
    )
    parser.add_argument(
        "--sandbox-name",
        default=None,
        help="Temporary sandbox name. Defaults to verify-<snapshot>-sandbox.",
    )
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        help="Leave the temporary sandbox running for manual inspection.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    config = resolve_daytona_config()
    runtime = DaytonaSandboxRuntime(config=config)

    sandbox_name = args.sandbox_name or f"verify-{args.snapshot}-sandbox"
    print(f"Creating a test sandbox from snapshot {args.snapshot!r}...")
    spec = runtime.build_sandbox_spec(name=sandbox_name, snapshot=args.snapshot)

    sandbox = None
    try:
        sandbox = await runtime.acreate_sandbox(spec=spec)
        print(f"Sandbox created successfully. ID: {sandbox.id}")

        print("\nVerifying environment inside the sandbox...")
        verify_code = """import sys
import dspy
import numpy
import pandas
import httpx
import pydantic

print(f"Python Version: {sys.version}")
print(f"DSPy Version: {dspy.__version__}")
print(f"numpy: {numpy.__version__}")
print(f"pandas: {pandas.__version__}")
print(f"httpx: {httpx.__version__}")
print(f"pydantic: {pydantic.__version__}")
"""
        sandbox.fs.upload_file(verify_code.encode("utf-8"), "/home/daytona/verify.py")
        result = sandbox.process.exec("python3 /home/daytona/verify.py")
        print("\n--- SANDBOX EXECUTION OUTPUT ---")
        print(result.result)
        print("--------------------------------")

        if result.exit_code != 0:
            print(f"\nVerification FAILED. Process exited with code {result.exit_code}", file=sys.stderr)
            return 1
        print("\nVerification passed.")
        return 0
    except Exception as exc:
        print(f"\nError during verification: {exc}", file=sys.stderr)
        return 1
    finally:
        if sandbox is not None and not args.keep_sandbox:
            print("\nCleaning up: deleting test sandbox...")
            try:
                sandbox.delete()
                print("Test sandbox deleted successfully.")
            except Exception as cleanup_exc:
                print(f"Cleanup warning: {cleanup_exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
