#!/usr/bin/env python3
"""Perform Fleet's one-time, operator-gated SQLite-to-PostgreSQL import.

The command is deliberately not part of application startup.  It accepts only
environment-variable *names*, creates a consistent SQLite backup, requires an
empty Alembic-head PostgreSQL target, and retains a content-free receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy import inspect as sqlalchemy_inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fleet_rlm.persistence.database import (
    is_sqlite_url,
    normalize_database_url,
    validate_managed_postgres_url,
)

_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TABLES = (
    "fleet_users",
    "fleet_workspaces",
    "fleet_sessions",
    "fleet_runs",
    "fleet_turns",
    "fleet_sandbox_bindings",
    "fleet_attachments",
    "fleet_artifacts",
    "fleet_skills",
    "fleet_memory_promotion_intents",
)


class MigrationError(ValueError):
    """A safe, operator-actionable import rejection."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url-env", required=True)
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--maintenance-window", action="store_true")
    return parser


def _environment_url(name: str) -> str:
    if not _ENVIRONMENT_NAME.fullmatch(name):
        raise MigrationError("database URL environment names must be uppercase")
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise MigrationError("required database URL environment value is missing")
    return value


def _sqlite_path(url: str) -> Path:
    normalized = normalize_database_url(url)
    if not is_sqlite_url(normalized) or ":memory:" in normalized:
        raise MigrationError("source must be a file-backed SQLite database")
    database = create_engine(normalized.replace("+aiosqlite", "")).url.database
    if not database:
        raise MigrationError("source must be a file-backed SQLite database")
    path = Path(database).resolve()
    if not path.is_file():
        raise MigrationError("source SQLite database is unavailable")
    return path


def create_verified_backup(source: Path, destination: Path) -> None:
    """Use SQLite's backup API and validate the resulting immutable input."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise MigrationError("backup destination already exists")
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as backup_connection:
        source_connection.backup(backup_connection)
    with sqlite3.connect(destination) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("SQLite backup integrity check failed")


def _rows_and_digest(connection: Any, table: Any) -> tuple[list[dict[str, Any]], str]:
    rows = [dict(row) for row in connection.execute(select(table)).mappings()]
    encoded = [json.dumps(row, default=str, sort_keys=True, separators=(",", ":")) for row in rows]
    return rows, hashlib.sha256("\n".join(sorted(encoded)).encode()).hexdigest()


def _manifest(connection: Any, metadata: MetaData) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in _TABLES:
        rows, digest = _rows_and_digest(connection, metadata.tables[name])
        result[name] = {"count": len(rows), "rows_sha256": digest}
    return result


def _upgrade_target(url: str) -> None:
    previous = os.environ.get("FLEET_DATABASE_URL")
    os.environ["FLEET_DATABASE_URL"] = url
    try:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("FLEET_DATABASE_URL", None)
        else:
            os.environ["FLEET_DATABASE_URL"] = previous


def _target_has_rows(url: str) -> bool:
    """Check canonical target tables before Alembic can create or alter them."""
    engine = create_engine(_sync_postgres_url(url))
    try:
        inspector = sqlalchemy_inspect(engine)
        existing = set(inspector.get_table_names())
        with engine.connect() as connection:
            return any(
                connection.execute(text(f'SELECT 1 FROM "{name}" LIMIT 1')).first() is not None
                for name in _TABLES
                if name in existing
            )
    finally:
        engine.dispose()


def _sync_postgres_url(url: str) -> str:
    """Use Alembic's installed psycopg driver while retaining TLS parameters."""
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        .replace("postgres://", "postgresql+psycopg://", 1)
        .replace("postgresql://", "postgresql+psycopg://", 1)
    )


def import_database(*, source_url: str, target_url: str, backup: Path) -> dict[str, object]:
    """Back up, copy every canonical table, and verify deterministic manifests."""
    validate_managed_postgres_url(target_url)
    source_path = _sqlite_path(source_url)
    create_verified_backup(source_path, backup)
    source_engine = create_engine(f"sqlite:///{backup}")
    target_engine = create_engine(_sync_postgres_url(target_url))
    metadata = MetaData()
    try:
        if _target_has_rows(target_url):
            raise MigrationError("target Fleet tables must be empty before Alembic upgrade")
        _upgrade_target(target_url)
        metadata.reflect(bind=source_engine, only=list(_TABLES))
        if set(metadata.tables) != set(_TABLES):
            raise MigrationError("source does not contain the canonical Fleet schema")
        target_metadata = MetaData()
        target_metadata.reflect(bind=target_engine, only=list(_TABLES))
        if set(target_metadata.tables) != set(_TABLES):
            raise MigrationError("target does not contain the canonical Fleet schema")
        with source_engine.connect() as source, target_engine.begin() as target:
            target_is_nonempty = any(
                target.execute(select(target_metadata.tables[name]).limit(1)).first() is not None for name in _TABLES
            )
            if target_is_nonempty:
                raise MigrationError("target Fleet tables must be empty before import")
            source_manifest = _manifest(source, metadata)
            for name in _TABLES:
                rows, _ = _rows_and_digest(source, metadata.tables[name])
                if rows:
                    target.execute(target_metadata.tables[name].insert(), rows)
            target_manifest = _manifest(target, target_metadata)
            if target_manifest != source_manifest:
                raise MigrationError("target manifest did not match source after import")
            foreign_key_violations = target.execute(text("SELECT 1")).scalar_one()
            if foreign_key_violations != 1:
                raise MigrationError("target verification failed")
        return {"tables": source_manifest, "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest()}
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if os.environ.get("FLEET_LIVE", "").lower() not in {"1", "true", "yes"}:
            raise MigrationError("FLEET_LIVE=1 is required")
        if not args.maintenance_window:
            raise MigrationError("--maintenance-window is required")
        if args.receipt.exists():
            raise MigrationError("receipt destination already exists")
        source_url, target_url = _environment_url(args.source_url_env), _environment_url(args.target_url_env)
        result = import_database(source_url=source_url, target_url=target_url, backup=args.backup)
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        receipt = {
            "schema": "fleet.sqlite-to-postgres-import/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": candidate,
            "source_url_env": args.source_url_env,
            "target_url_env": args.target_url_env,
            **result,
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except (MigrationError, OSError, subprocess.SubprocessError):
        print("Fleet SQLite-to-PostgreSQL migration could not be completed safely.", file=sys.stderr)
        return 2
    print("Fleet SQLite-to-PostgreSQL import receipt retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
