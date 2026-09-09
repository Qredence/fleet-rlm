"""Retain a durable, content-free database-head inventory receipt.

Read-only operator instrument for P1A.01: reports the repository Alembic
head graph alongside one deployed target's current ``alembic_version`` and
server version. Never migrates, never rewrites history, never stores the
database URL or driver exception text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TARGET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REVISION_PATTERN = re.compile(r"[a-f0-9]{8,32}")
VERSION_PATTERN = re.compile(r"[0-9]{5,8}")


class InventoryError(ValueError):
    pass


def validate_target_label(value: str) -> str:
    if not TARGET_PATTERN.fullmatch(value):
        raise InventoryError("target label must be 1-64 chars of [A-Za-z0-9._-], starting alphanumeric")
    return value


def repo_heads(repo_root: Path) -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    ordered = sorted(str(head) for head in heads)
    if not ordered or any(not REVISION_PATTERN.fullmatch(head) for head in ordered):
        raise InventoryError("repository head inventory failed")
    return ordered


def repo_ancestors(repo_root: Path) -> set[str]:
    """Return every revision reachable from every current repository head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    script = ScriptDirectory.from_config(config)
    reachable: set[str] = set()
    for head in script.get_heads():
        for revision in script.iterate_revisions(head, "base"):
            reachable.add(str(revision.revision))
    return reachable


def compare_heads(repo: list[str], current: list[str], *, ancestors: set[str] | None = None) -> dict[str, object]:
    repo_set, current_set = set(repo), set(current)
    if current_set == repo_set:
        strategy = "none_required"
    elif current_set and ancestors is not None and current_set <= ancestors:
        strategy = "additive_required"
    else:
        strategy = "manual_review"
    known = ancestors if ancestors is not None else repo_set
    return {
        "matches_repo_heads": current_set == repo_set,
        "extra_heads": sorted(current_set - known),
        "missing_heads": sorted(repo_set - current_set),
        "migration_strategy": strategy,
        "rewrite_history": False,
    }


async def inspect_target(database_url: str) -> dict[str, object]:
    from sqlalchemy import inspect, text

    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        is_sqlite_url,
    )

    engine = create_async_engine_from_url(database_url)
    try:
        backend = "sqlite" if is_sqlite_url(database_url) else "postgresql"
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                has_table = await connection.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version"))
                if not has_table:
                    raise InventoryError("database revision is not tracked")
                result = await connection.execute(text("SELECT version_num FROM alembic_version"))
                current = sorted({str(row) for row in result.scalars().all()})
                if not current or any(not REVISION_PATTERN.fullmatch(rev) for rev in current):
                    raise InventoryError("database head inventory failed")
                if backend == "sqlite":
                    version_row = await connection.execute(text("SELECT sqlite_version()"))
                    server_version = str(version_row.scalar_one())
                    server_version_num: str | None = None
                else:
                    version_row = await connection.execute(text("SHOW server_version_num"))
                    server_version = str(version_row.scalar_one())
                    if not VERSION_PATTERN.fullmatch(server_version):
                        raise InventoryError("database version inventory failed")
                    server_version_num = server_version
        except InventoryError:
            raise
        except Exception as exc:
            raise InventoryError("database inventory failed") from exc
    finally:
        await engine.dispose()
    return {
        "backend": backend,
        "server_version_num": server_version_num,
        "server_version": server_version if backend == "sqlite" else None,
        "alembic_heads": current,
    }


def build_receipt(
    *,
    target: str,
    repo: list[str],
    observed: dict[str, object],
    git_sha: str,
    dirty: bool,
    ancestors: set[str],
) -> dict[str, object]:
    current = list(observed["alembic_heads"])
    return {
        "schema": "fleet.db-head-inventory/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {"git_sha": git_sha, "dirty": dirty},
        "target": {"label": target, "backend": observed["backend"]},
        "repository": {"heads": repo},
        "database": {
            "server_version_num": observed["server_version_num"],
            "server_version": observed["server_version"],
            "alembic_heads": current,
        },
        "comparison": compare_heads(repo, current, ancestors=ancestors),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--target", type=str, required=True, help="Non-secret target label")
    parser.add_argument("--database-url-env", type=str, default="FLEET_DATABASE_URL")
    args = parser.parse_args(argv)
    import os

    try:
        target = validate_target_label(args.target)
        raw_url = os.environ.get(args.database_url_env, "")
        if not raw_url:
            raise InventoryError("database URL is not configured")
        heads = repo_heads(ROOT)
        ancestors = repo_ancestors(ROOT)
        observed = asyncio.run(inspect_target(raw_url))
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
        receipt = build_receipt(
            target=target,
            repo=heads,
            observed=observed,
            git_sha=sha,
            dirty=dirty,
            ancestors=ancestors,
        )
    except InventoryError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print("inventory support failed", file=sys.stderr)
        raise SystemExit(2) from error
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.receipt.open("x", encoding="utf-8") as destination:
            json.dump(receipt, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError:
        print("receipt already exists; refusing replacement", file=sys.stderr)
        return 2
    print("Database head inventory retained; strategy=" + str(receipt["comparison"]["migration_strategy"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
