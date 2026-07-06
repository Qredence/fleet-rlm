#!/usr/bin/env python3
"""Create or refresh the default Daytona base snapshot."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from fleet_rlm.integrations.daytona.snapshots import (
    DEFAULT_SNAPSHOT_BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_PACKAGES,
    create_snapshot,
    delete_snapshot,
    get_snapshot,
    list_snapshots,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        default=DEFAULT_SNAPSHOT_NAME,
        help=f"Snapshot name to create or refresh. Defaults to {DEFAULT_SNAPSHOT_NAME!r}.",
    )
    parser.add_argument(
        "--base-image",
        default=DEFAULT_SNAPSHOT_BASE_IMAGE,
        help=f"Base image passed to Daytona Image.base. Defaults to {DEFAULT_SNAPSHOT_BASE_IMAGE!r}.",
    )
    parser.add_argument(
        "--package",
        action="append",
        dest="packages",
        help="Python package to preinstall. Repeat to override the default package set.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Delete and recreate the snapshot if it already exists.",
    )
    parser.add_argument(
        "--list-after",
        action="store_true",
        help="List Daytona snapshots after creation.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    packages = args.packages if args.packages is not None else DEFAULT_SNAPSHOT_PACKAGES
    print(f"Ensuring Daytona snapshot {args.name!r}...")
    print(f"Base image: {args.base_image}")
    print(f"Packages: {packages}")
    print(f"Refresh existing snapshot: {args.refresh}")

    try:
        existing = get_snapshot(args.name)
        if existing is not None and not args.refresh:
            print(f"Snapshot {args.name!r} already exists (ID: {existing['id']}).")
            print("Pass --refresh to delete and recreate it.")
            return 0
        if existing is not None:
            print(f"Deleting existing snapshot {args.name!r} (ID: {existing['id']})...")
            delete_snapshot(args.name)
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                if get_snapshot(args.name) is None:
                    break
                time.sleep(2.0)
            else:
                print("Warning: timed out waiting for snapshot deletion; attempting build anyway.")

        def log_handler(message: str) -> None:
            sys.stdout.write(message)
            sys.stdout.flush()

        result = create_snapshot(
            name=args.name,
            base_image=args.base_image,
            packages=packages,
            on_logs=log_handler,
        )
        print("\nSuccess.")
        print(f"Snapshot details: {result}")

        if args.list_after:
            print("\nAvailable snapshots:")
            for snapshot in list_snapshots():
                print(f"- {snapshot['name']} (ID: {snapshot['id']}, State: {snapshot['state']})")
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
