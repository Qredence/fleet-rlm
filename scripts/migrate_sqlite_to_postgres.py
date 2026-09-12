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
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import SQLAlchemyError

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
    "fleet_warm_pool_ownership",
    "fleet_attachments",
    "fleet_artifacts",
    "fleet_skills",
    "fleet_memory_promotion_intents",
)
_LEGACY_GENERATION_TABLE = "fleet_sandbox_bindings"

_STATUS_VALUES: dict[str, frozenset[str]] = {
    "fleet_sessions": frozenset({"active", "archived"}),
    "fleet_runs": frozenset({"running", "settling", "completed", "failed", "cancelled", "timeout"}),
    "fleet_memory_promotion_intents": frozenset({"pending", "completing", "completed", "failed"}),
}


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
        violation = connection.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise MigrationError("SQLite backup foreign-key check failed")


def _validate_statuses(connection: Any, metadata: MetaData) -> None:
    """Reject legacy invalid enum-like values rather than repairing them."""
    for table_name, allowed in _STATUS_VALUES.items():
        table = metadata.tables.get(table_name)
        if table is None:
            continue
        values = connection.execute(select(table.c.status).distinct()).scalars()
        invalid = sorted({value for value in values if value not in allowed})
        if invalid:
            raise MigrationError(f"source contains invalid {table_name} status values")


def _canonical_value(value: Any) -> Any:
    """Normalize values whose SQLite and PostgreSQL adapters represent differently."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MigrationError("source contains a non-finite numeric value")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime_time):
        normalized_time = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return normalized_time.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return str(value)


def _digest_rows(rows: Sequence[Mapping[str, Any]], table: Any) -> str:
    """Hash canonical rows in primary-key order for cross-backend comparison."""
    primary_keys = tuple(column.key for column in table.primary_key.columns)
    canonical_rows = [{str(key): _canonical_value(value) for key, value in row.items()} for row in rows]
    canonical_rows.sort(
        key=lambda row: (
            tuple(json.dumps(row.get(key), sort_keys=True, separators=(",", ":")) for key in primary_keys)
            or (json.dumps(row, sort_keys=True, separators=(",", ":")),)
        )
    )
    encoded = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in canonical_rows]
    return hashlib.sha256("\n".join(encoded).encode()).hexdigest()


def _rows_and_digest(connection: Any, table: Any) -> tuple[list[dict[str, Any]], str]:
    rows = [dict(row) for row in connection.execute(select(table)).mappings()]
    return rows, _digest_rows(rows, table)


def _rows_for_target(connection: Any, source_table: Any, target_table: Any) -> list[dict[str, Any]]:
    """Project legacy rows into the current target shape without inventing data.

    ``generation`` was added to ``fleet_sandbox_bindings`` after the first
    SQLite schema. A pre-generation source is semantically generation one for
    every existing binding; make that compatibility explicit before both the
    source manifest and the target insert are computed.
    """
    rows = [dict(row) for row in connection.execute(select(source_table)).mappings()]
    source_keys = {column.key for column in source_table.columns}
    target_keys = {column.key for column in target_table.columns}
    generation_is_legacy_binding = (
        source_table.name == _LEGACY_GENERATION_TABLE
        and "generation" in target_keys
        and "generation" not in source_keys
    )
    if generation_is_legacy_binding:
        for row in rows:
            row["generation"] = 1
    # Keep only columns accepted by the target. The canonical schemas are
    # expected to match apart from the compatibility field above; dropping any
    # other mismatch would hide a migration error, so reject it explicitly.
    extra = source_keys - target_keys
    if extra:
        raise MigrationError("source schema has unsupported Fleet columns")
    # Validate the reflected schema even when the source table is empty. Using
    # the first row here would let an empty, incomplete table pass the
    # canonical-schema gate simply because there was no row to inspect.
    missing = target_keys - source_keys
    if missing and not (generation_is_legacy_binding and missing == {"generation"}):
        raise MigrationError("source schema is missing required Fleet columns")
    return [{key: value for key, value in row.items() if key in target_keys} for row in rows]


def _verify_sample_reconstruction(
    source: Any,
    target: Any,
    source_metadata: MetaData,
    target_metadata: MetaData,
) -> dict[str, object]:
    """Verify one content-free Session→Run→Turn reconstruction after import."""
    source_sessions = source_metadata.tables["fleet_sessions"]
    source_runs = source_metadata.tables["fleet_runs"]
    source_turns = source_metadata.tables["fleet_turns"]
    target_sessions = target_metadata.tables["fleet_sessions"]
    target_runs = target_metadata.tables["fleet_runs"]
    target_turns = target_metadata.tables["fleet_turns"]
    sample = (
        source.execute(
            select(source_sessions.c.id, source_runs.c.id.label("run_id"))
            .join(source_runs, source_runs.c.session_id == source_sessions.c.id)
            .order_by(source_runs.c.created_at, source_runs.c.id)
            .limit(1)
        )
        .mappings()
        .first()
    )
    if sample is None:
        return {"status": "not-exercised", "turn_count": 0}
    session_id = sample["id"]
    run_id = sample["run_id"]
    source_session = (
        source.execute(select(source_sessions).where(source_sessions.c.id == session_id)).mappings().first()
    )
    target_session = (
        target.execute(select(target_sessions).where(target_sessions.c.id == session_id)).mappings().first()
    )
    source_run = source.execute(select(source_runs).where(source_runs.c.id == run_id)).mappings().first()
    target_run = target.execute(select(target_runs).where(target_runs.c.id == run_id)).mappings().first()
    source_turn_rows = list(
        source.execute(
            select(source_turns).where(source_turns.c.run_id == run_id).order_by(source_turns.c.sequence)
        ).mappings()
    )
    target_turn_rows = list(
        target.execute(
            select(target_turns).where(target_turns.c.run_id == run_id).order_by(target_turns.c.sequence)
        ).mappings()
    )
    if source_session is None or target_session is None or source_run is None or target_run is None:
        raise MigrationError("target sample Session/Run reconstruction was incomplete")
    if _digest_rows([source_session], source_sessions) != _digest_rows([target_session], target_sessions):
        raise MigrationError("target sample Session did not match source")
    if _digest_rows([source_run], source_runs) != _digest_rows([target_run], target_runs):
        raise MigrationError("target sample Run did not match source")
    if _digest_rows(source_turn_rows, source_turns) != _digest_rows(target_turn_rows, target_turns):
        raise MigrationError("target sample Turn history did not match source")
    return {"status": "passed", "turn_count": len(source_turn_rows)}


def _manifest(
    connection: Any,
    metadata: MetaData,
    *,
    digest_metadata: MetaData | None = None,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in _TABLES:
        table = metadata.tables.get(name)
        if table is None:
            rows, digest = [], hashlib.sha256(b"").hexdigest()
        elif digest_metadata is not None and name in digest_metadata.tables:
            target_table = digest_metadata.tables[name]
            rows = _rows_for_target(connection, table, target_table)
            digest = _digest_rows(rows, target_table)
        else:
            rows, digest = _rows_and_digest(connection, table)
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


def _target_is_at_head(engine: Any) -> bool:
    """Return whether the operator target has exactly this repository's heads."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    expected = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    return actual == expected


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


def _target_has_fleet_schema(url: str) -> bool:
    """Return whether the target already has any canonical Fleet table."""
    engine = create_engine(_sync_postgres_url(url))
    try:
        return bool(set(sqlalchemy_inspect(engine).get_table_names()).intersection(_TABLES))
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
        # An empty target is initialized by this operator-only tool. Once any
        # Fleet table exists, though, silently migrating an unknown revision
        # would make an import receipt conceal an unrehearsed schema change.
        if _target_has_fleet_schema(target_url) and not _target_is_at_head(target_engine):
            raise MigrationError("existing target Fleet schema is not at this candidate's Alembic head")
        _upgrade_target(target_url)
        if not _target_is_at_head(target_engine):
            raise MigrationError("target Alembic revision is not at this candidate's head")
        source_tables = set(sqlalchemy_inspect(source_engine).get_table_names())
        metadata.reflect(bind=source_engine, only=sorted(source_tables.intersection(_TABLES)))
        required_source_tables = set(_TABLES) - {"fleet_warm_pool_ownership"}
        if not required_source_tables.issubset(metadata.tables):
            raise MigrationError("source does not contain the canonical Fleet schema")
        target_metadata = MetaData()
        target_tables = set(sqlalchemy_inspect(target_engine).get_table_names())
        target_metadata.reflect(bind=target_engine, only=sorted(target_tables.intersection(_TABLES)))
        if set(target_metadata.tables) != set(_TABLES):
            raise MigrationError("target does not contain the canonical Fleet schema")
        with source_engine.connect() as source, target_engine.begin() as target:
            target_is_nonempty = any(
                target.execute(select(target_metadata.tables[name]).limit(1)).first() is not None for name in _TABLES
            )
            if target_is_nonempty:
                raise MigrationError("target Fleet tables must be empty before import")
            _validate_statuses(source, metadata)
            source_manifest = _manifest(source, metadata, digest_metadata=target_metadata)
            for name in _TABLES:
                source_table = metadata.tables.get(name)
                if source_table is None:
                    continue
                rows = _rows_for_target(source, source_table, target_metadata.tables[name])
                if rows:
                    target.execute(target_metadata.tables[name].insert(), rows)
            # PostgreSQL normally checks these constraints at each insert;
            # force any deferred constraints before calculating the receipt so
            # the transaction cannot be reported as verified while a lineage
            # violation is still pending.
            target.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            target_manifest = _manifest(target, target_metadata)
            if target_manifest != source_manifest:
                raise MigrationError("target manifest did not match source after import")
            sample_reconstruction = _verify_sample_reconstruction(
                source,
                target,
                metadata,
                target_metadata,
            )
        return {
            "tables": source_manifest,
            "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "sample_reconstruction": sample_reconstruction,
        }
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
    except (MigrationError, OSError, subprocess.SubprocessError, SQLAlchemyError, CommandError):
        print("Fleet SQLite-to-PostgreSQL migration could not be completed safely.", file=sys.stderr)
        return 2
    except Exception:
        # Keep unexpected driver/extension failures from leaking connection
        # details or tracebacks into an operator-facing command. The target
        # transaction is owned by ``import_database`` and is rolled back by
        # its context manager before this boundary is reached.
        print("Fleet SQLite-to-PostgreSQL migration could not be completed safely.", file=sys.stderr)
        return 2
    print("Fleet SQLite-to-PostgreSQL import receipt retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
