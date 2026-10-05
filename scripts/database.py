from __future__ import annotations

# Alembic database initialization.
import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv


def upgrade_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--database-url", help="Overrides FLEET_DATABASE_URL for this process")
    return parser


def upgrade_main(argv: list[str] | None = None) -> int:
    args = upgrade_build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    load_dotenv(args.env_file or root / ".env", override=False)
    database_url = args.database_url or os.getenv("FLEET_DATABASE_URL")
    if not database_url:
        print("FLEET_DATABASE_URL is required", file=sys.stderr)
        return 1

    os.environ["FLEET_DATABASE_URL"] = database_url
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    print("Fleet RLM database upgraded to Alembic head.")
    return 0


# Explicit SQLite-to-PostgreSQL data import.


import hashlib
import json
import math
import re
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import SQLAlchemyError

IMPORT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(IMPORT_ROOT / "src"))

from fleet_rlm.persistence.database import (
    is_sqlite_url,
    normalize_database_url,
    validate_managed_postgres_url,
)

IMPORT_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

IMPORT_TABLES = (
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

IMPORT_LEGACY_GENERATION_TABLE = "fleet_sandbox_bindings"

IMPORT_STATUS_VALUES: dict[str, frozenset[str]] = {
    "fleet_sessions": frozenset({"active", "archived"}),
    "fleet_runs": frozenset({"running", "settling", "completed", "failed", "cancelled", "timeout"}),
    "fleet_memory_promotion_intents": frozenset({"pending", "completing", "completed", "failed"}),
}


class MigrationError(ValueError):
    """A safe, operator-actionable import rejection."""


def import__parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url-env", required=True)
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--maintenance-window", action="store_true")
    return parser


def import__environment_url(name: str) -> str:
    if not IMPORT_ENVIRONMENT_NAME.fullmatch(name):
        raise MigrationError("database URL environment names must be uppercase")
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise MigrationError("required database URL environment value is missing")
    return value


def import__sqlite_path(url: str) -> Path:
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


def import_create_verified_backup(source: Path, destination: Path) -> None:
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


def import__validate_statuses(connection: Any, metadata: MetaData) -> None:
    """Reject legacy invalid enum-like values rather than repairing them."""
    for table_name, allowed in IMPORT_STATUS_VALUES.items():
        table = metadata.tables.get(table_name)
        if table is None:
            continue
        values = connection.execute(select(table.c.status).distinct()).scalars()
        invalid = sorted({value for value in values if value not in allowed})
        if invalid:
            raise MigrationError(f"source contains invalid {table_name} status values")


def import__canonical_value(value: Any) -> Any:
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
        return {
            str(key): import__canonical_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [import__canonical_value(item) for item in value]
    return str(value)


def import__digest_rows(rows: Sequence[Mapping[str, Any]], table: Any) -> str:
    """Hash canonical rows in primary-key order for cross-backend comparison."""
    primary_keys = tuple(column.key for column in table.primary_key.columns)
    canonical_rows = [{str(key): import__canonical_value(value) for key, value in row.items()} for row in rows]
    canonical_rows.sort(
        key=lambda row: (
            tuple(json.dumps(row.get(key), sort_keys=True, separators=(",", ":")) for key in primary_keys)
            or (json.dumps(row, sort_keys=True, separators=(",", ":")),)
        )
    )
    encoded = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in canonical_rows]
    return hashlib.sha256("\n".join(encoded).encode()).hexdigest()


def import__rows_and_digest(connection: Any, table: Any) -> tuple[list[dict[str, Any]], str]:
    rows = [dict(row) for row in connection.execute(select(table)).mappings()]
    return rows, import__digest_rows(rows, table)


def import__rows_for_target(connection: Any, source_table: Any, target_table: Any) -> list[dict[str, Any]]:
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
        source_table.name == IMPORT_LEGACY_GENERATION_TABLE
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


def import__verify_sample_reconstruction(
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
    if import__digest_rows([source_session], source_sessions) != import__digest_rows([target_session], target_sessions):
        raise MigrationError("target sample Session did not match source")
    if import__digest_rows([source_run], source_runs) != import__digest_rows([target_run], target_runs):
        raise MigrationError("target sample Run did not match source")
    if import__digest_rows(source_turn_rows, source_turns) != import__digest_rows(target_turn_rows, target_turns):
        raise MigrationError("target sample Turn history did not match source")
    return {"status": "passed", "turn_count": len(source_turn_rows)}


def import__manifest(
    connection: Any,
    metadata: MetaData,
    *,
    digest_metadata: MetaData | None = None,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in IMPORT_TABLES:
        table = metadata.tables.get(name)
        if table is None:
            rows, digest = [], hashlib.sha256(b"").hexdigest()
        elif digest_metadata is not None and name in digest_metadata.tables:
            target_table = digest_metadata.tables[name]
            rows = import__rows_for_target(connection, table, target_table)
            digest = import__digest_rows(rows, target_table)
        else:
            rows, digest = import__rows_and_digest(connection, table)
        result[name] = {"count": len(rows), "rows_sha256": digest}
    return result


def import__upgrade_target(url: str) -> None:
    previous = os.environ.get("FLEET_DATABASE_URL")
    os.environ["FLEET_DATABASE_URL"] = url
    try:
        config = Config(str(IMPORT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(IMPORT_ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("FLEET_DATABASE_URL", None)
        else:
            os.environ["FLEET_DATABASE_URL"] = previous


def import__target_is_at_head(engine: Any) -> bool:
    """Return whether the operator target has exactly this repository's heads."""
    config = Config(str(IMPORT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(IMPORT_ROOT / "migrations"))
    expected = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    return actual == expected


def import__target_has_rows(url: str) -> bool:
    """Check canonical target tables before Alembic can create or alter them."""
    engine = create_engine(import__sync_postgres_url(url))
    try:
        inspector = sqlalchemy_inspect(engine)
        existing = set(inspector.get_table_names())
        with engine.connect() as connection:
            return any(
                connection.execute(text(f'SELECT 1 FROM "{name}" LIMIT 1')).first() is not None
                for name in IMPORT_TABLES
                if name in existing
            )
    finally:
        engine.dispose()


def import__target_has_fleet_schema(url: str) -> bool:
    """Return whether the target already has any canonical Fleet table."""
    engine = create_engine(import__sync_postgres_url(url))
    try:
        return bool(set(sqlalchemy_inspect(engine).get_table_names()).intersection(IMPORT_TABLES))
    finally:
        engine.dispose()


def import__sync_postgres_url(url: str) -> str:
    """Use Alembic's installed psycopg driver while retaining TLS parameters."""
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        .replace("postgres://", "postgresql+psycopg://", 1)
        .replace("postgresql://", "postgresql+psycopg://", 1)
    )


def import_import_database(*, source_url: str, target_url: str, backup: Path) -> dict[str, object]:
    """Back up, copy every canonical table, and verify deterministic manifests."""
    validate_managed_postgres_url(target_url)
    source_path = import__sqlite_path(source_url)
    import_create_verified_backup(source_path, backup)
    source_engine = create_engine(f"sqlite:///{backup}")
    target_engine = create_engine(import__sync_postgres_url(target_url))
    metadata = MetaData()
    try:
        if import__target_has_rows(target_url):
            raise MigrationError("target Fleet tables must be empty before Alembic upgrade")
        # An empty target is initialized by this operator-only tool. Once any
        # Fleet table exists, though, silently migrating an unknown revision
        # would make an import receipt conceal an unrehearsed schema change.
        if import__target_has_fleet_schema(target_url) and not import__target_is_at_head(target_engine):
            raise MigrationError("existing target Fleet schema is not at this candidate's Alembic head")
        import__upgrade_target(target_url)
        if not import__target_is_at_head(target_engine):
            raise MigrationError("target Alembic revision is not at this candidate's head")
        source_tables = set(sqlalchemy_inspect(source_engine).get_table_names())
        metadata.reflect(bind=source_engine, only=sorted(source_tables.intersection(IMPORT_TABLES)))
        required_source_tables = set(IMPORT_TABLES) - {"fleet_warm_pool_ownership"}
        if not required_source_tables.issubset(metadata.tables):
            raise MigrationError("source does not contain the canonical Fleet schema")
        target_metadata = MetaData()
        target_tables = set(sqlalchemy_inspect(target_engine).get_table_names())
        target_metadata.reflect(bind=target_engine, only=sorted(target_tables.intersection(IMPORT_TABLES)))
        if set(target_metadata.tables) != set(IMPORT_TABLES):
            raise MigrationError("target does not contain the canonical Fleet schema")
        with source_engine.connect() as source, target_engine.begin() as target:
            target_is_nonempty = any(
                target.execute(select(target_metadata.tables[name]).limit(1)).first() is not None
                for name in IMPORT_TABLES
            )
            if target_is_nonempty:
                raise MigrationError("target Fleet tables must be empty before import")
            import__validate_statuses(source, metadata)
            source_manifest = import__manifest(source, metadata, digest_metadata=target_metadata)
            for name in IMPORT_TABLES:
                source_table = metadata.tables.get(name)
                if source_table is None:
                    continue
                rows = import__rows_for_target(source, source_table, target_metadata.tables[name])
                if rows:
                    target.execute(target_metadata.tables[name].insert(), rows)
            # PostgreSQL normally checks these constraints at each insert;
            # force any deferred constraints before calculating the receipt so
            # the transaction cannot be reported as verified while a lineage
            # violation is still pending.
            target.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            target_manifest = import__manifest(target, target_metadata)
            if target_manifest != source_manifest:
                raise MigrationError("target manifest did not match source after import")
            sample_reconstruction = import__verify_sample_reconstruction(
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


def import_main(argv: list[str] | None = None) -> int:
    args = import__parser().parse_args(argv)
    try:
        if os.environ.get("FLEET_LIVE", "").lower() not in {"1", "true", "yes"}:
            raise MigrationError("FLEET_LIVE=1 is required")
        if not args.maintenance_window:
            raise MigrationError("--maintenance-window is required")
        if args.receipt.exists():
            raise MigrationError("receipt destination already exists")
        source_url, target_url = (
            import__environment_url(args.source_url_env),
            import__environment_url(args.target_url_env),
        )
        result = import_import_database(source_url=source_url, target_url=target_url, backup=args.backup)
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=IMPORT_ROOT, text=True).strip()
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


# Managed PostgreSQL readiness preflight.


import asyncio
import re
import sys
from pathlib import Path

PREFLIGHT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PREFLIGHT_ROOT / "src"))

from fleet_rlm.persistence.preflight import ManagedDatabasePreflightError, inspect_managed_postgres

PREFLIGHT_TARGET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def preflight__resolve_mlflow_tracking_uri(explicit: str | None) -> str:
    """Resolve the selected MLflow endpoint without accepting an unknown backend."""
    if explicit is not None:
        value = explicit.strip()
    else:
        try:
            from fleet_rlm.config.loader import load_runtime_settings

            value = (load_runtime_settings().mlflow_tracking_uri or "").strip()
        except Exception as exc:
            raise ManagedDatabasePreflightError("configured MLflow tracking URI could not be resolved") from exc
    if not value:
        raise ManagedDatabasePreflightError("an MLflow tracking URI is required to prove storage separation")
    return value


def preflight_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--target", required=True, help="Non-secret target label")
    parser.add_argument("--database-url-env", default="FLEET_DATABASE_URL")
    parser.add_argument("--mlflow-tracking-uri", default=None)
    args = parser.parse_args(argv)
    if not PREFLIGHT_TARGET_PATTERN.fullmatch(args.target):
        print("target label must be 1-64 chars of [A-Za-z0-9._-], starting alphanumeric", file=sys.stderr)
        return 2
    if os.environ.get("FLEET_LIVE") != "1":
        print("set FLEET_LIVE=1 for the managed database preflight", file=sys.stderr)
        return 2
    database_url = os.environ.get(args.database_url_env, "")
    if not database_url:
        print("database URL is not configured", file=sys.stderr)
        return 2
    try:
        mlflow_tracking_uri = preflight__resolve_mlflow_tracking_uri(args.mlflow_tracking_uri)
        observed = asyncio.run(
            inspect_managed_postgres(database_url, repo_root=PREFLIGHT_ROOT, mlflow_tracking_uri=mlflow_tracking_uri)
        )
        if observed.mlflow_storage_separate is not True:
            raise ManagedDatabasePreflightError(
                "Fleet and MLflow storage separation could not be proven for the selected topology"
            )
        candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PREFLIGHT_ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PREFLIGHT_ROOT, text=True))
        receipt = {
            "schema": "fleet.lakebase-preflight/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": {"git_sha": candidate, "dirty": dirty},
            "target": {"label": args.target},
            "observed": observed.as_dict(),
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as destination:
            json.dump(receipt, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError:
        print("receipt already exists; refusing replacement", file=sys.stderr)
        return 2
    except (ManagedDatabasePreflightError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, ManagedDatabasePreflightError) else "preflight support failed"
        print(message, file=sys.stderr)
        return 2
    print("Lakebase preflight passed")
    return 0


# Explicit PostgreSQL certification and sanitized query-plan receipts.


import re
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

POSTGRES_ROOT = Path(__file__).resolve().parents[1]

POSTGRES_TEST_PATH = "tests/live/test_postgres_contention.py"

POSTGRES_QUERY_TEST_PATH = "tests/live/test_postgres_query_plans.py"

POSTGRES_QUERY_OPERATIONS = ("sessions", "history", "replay", "recovery", "outbox")

POSTGRES_SCENARIOS = {
    "test_postgres_concurrent_claims_have_one_owner[duplicate]": "duplicate_claim",
    "test_postgres_concurrent_claims_have_one_owner[conflicting_input]": "conflicting_input_claim",
    "test_postgres_concurrent_claims_have_one_owner[active_run]": "active_run_claim",
    "test_postgres_cancel_settlement_races_commit": "cancel_settlement_commit",
    "test_postgres_recovery_owner_cas_fences_stale_commit": "recovery_owner_stale_commit",
    "test_postgres_outbox_claims_are_disjoint": "outbox_disjoint_claim",
}


def postgres_project_query_plan(plan: dict[str, object], *, depth: int = 0) -> dict[str, object]:
    """Retain bounded planner topology and measurements without SQL literals."""
    if depth > 32:
        raise ValueError("query plan exceeds depth bound")
    result: dict[str, object] = {}
    node = plan.get("Node Type")
    allowed = {
        "Aggregate",
        "Append",
        "Bitmap Heap Scan",
        "Bitmap Index Scan",
        "BitmapAnd",
        "BitmapOr",
        "Gather",
        "Gather Merge",
        "Hash",
        "Hash Join",
        "Index Only Scan",
        "Index Scan",
        "Limit",
        "LockRows",
        "Materialize",
        "Memoize",
        "Merge Join",
        "Nested Loop",
        "Result",
        "Seq Scan",
        "Sort",
        "Subquery Scan",
        "Unique",
        "WindowAgg",
        "Incremental Sort",
    }
    result["Node Type"] = node if isinstance(node, str) and node in allowed else "Other"
    for key in ("Startup Cost", "Total Cost", "Plan Rows", "Plan Width"):
        value = plan.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            result[key] = value
    children = plan.get("Plans", [])
    if not isinstance(children, list) or len(children) > 128:
        raise ValueError("query plan children exceed bound")
    if children:
        result["Plans"] = [
            postgres_project_query_plan(child, depth=depth + 1) for child in children if isinstance(child, dict)
        ]
    return result


def postgres_summarize_report(xml: str, *, exit_code: int) -> dict[str, object]:
    """Project only known test identities and safe database provenance from JUnit."""
    outcomes = dict.fromkeys(POSTGRES_SCENARIOS.values(), "not_run")
    provenance: dict[str, set[str]] = {"server_version_num": set(), "alembic_heads": set()}
    root = ElementTree.fromstring(xml)
    query_plans = {}
    for operation in POSTGRES_QUERY_OPERATIONS:
        cases = [
            case
            for case in root.iter("testcase")
            if case.get("name") == f"test_postgres_repository_query_plan[{operation}]"
        ]
        if not cases:
            continue
        valid = len(cases) == 1 and not any(cases[0].find(tag) is not None for tag in ("failure", "error", "skipped"))
        properties = [
            prop.get("value", "")
            for prop in root.iter("property")
            if prop.get("name") == f"fleet.postgres.query_plan.{operation}"
        ]
        projected = []
        samples = None
        if valid and len(properties) == 1 and len(properties[0]) <= 256_000:
            try:
                payload = json.loads(properties[0])
                samples = payload["fixture_samples"]
                if type(samples) is not int or not 1 <= samples <= 1000:
                    raise ValueError("invalid fixture scale")
                for entry in payload["plans"]:
                    digest = entry["statement_sha256"]
                    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                        raise ValueError("invalid statement digest")
                    projected.append({"statement_sha256": digest, "plan": postgres_project_query_plan(entry["plan"])})
            except (ValueError, TypeError, KeyError, AttributeError):
                projected = []
                samples = None
        query_plans[operation] = {
            "status": "passed" if valid and projected else "incomplete",
            "fixture_samples": samples,
            "basis": "synthetic",
            "plans": projected,
        }
    seen: set[str] = set()
    for case in root.iter("testcase"):
        name = POSTGRES_SCENARIOS.get(case.get("name", ""))
        if name is None:
            continue
        status = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        if name in seen:
            status = "failed"
        outcomes[name] = status
        seen.add(name)
    for prop in root.iter("property"):
        if not prop.get("name", "").startswith("fleet.postgres."):
            continue
        key = prop.get("name", "").removeprefix("fleet.postgres.")
        value = prop.get("value", "")
        pattern = r"[0-9]{5,8}" if key == "server_version_num" else r"[a-f0-9]{8,32}(?:,[a-f0-9]{8,32})*"
        if key in provenance and re.fullmatch(pattern, value):
            provenance[key].add(value)
    database = {key: next(iter(values)) if len(values) == 1 else None for key, values in provenance.items()}
    complete = exit_code == 0 and all(value == "passed" for value in outcomes.values()) and all(database.values())
    return {
        "database": database,
        "scenarios": outcomes,
        "result": {
            "passed": list(outcomes.values()).count("passed"),
            "failed": list(outcomes.values()).count("failed"),
            "skipped": list(outcomes.values()).count("skipped"),
            "complete_six_scenario_campaign": complete,
        },
        "query_plans": query_plans or "not_exercised",
    }


def postgres_preflight() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise ValueError("FLEET_LIVE=1 is required")
    if not os.environ.get("FLEET_DATABASE_URL", "").startswith(("postgres://", "postgresql")):
        raise ValueError("An exported PostgreSQL FLEET_DATABASE_URL is required")
    if os.environ.get("FLEET_TEST_DATABASE_EXCLUSIVE") != "1":
        raise ValueError("An explicitly designated exclusive test database is required")


def postgres_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180, choices=range(30, 601), metavar="30..600")
    parser.add_argument("--query-plans", action="store_true", help="Also collect synthetic repository query plans")
    parser.add_argument("--query-plan-samples", type=int, default=64, choices=range(1, 1001), metavar="1..1000")
    args = parser.parse_args(argv)
    try:
        postgres_preflight()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    # Reserve the destination before contacting the database. Do not overwrite
    # a prior receipt, including a negative one.
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x", encoding="utf-8") as destination:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=POSTGRES_ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=POSTGRES_ROOT, text=True))
        with TemporaryDirectory(prefix="fleet-postgres-") as temporary:
            report = Path(temporary) / "junit.xml"
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        POSTGRES_TEST_PATH,
                        *([POSTGRES_QUERY_TEST_PATH] if args.query_plans else []),
                        "-q",
                        "--tb=no",
                        f"--junitxml={report}",
                    ],
                    cwd=POSTGRES_ROOT,
                    capture_output=True,
                    timeout=args.timeout,
                    check=False,
                    env={**os.environ, "FLEET_POSTGRES_QUERY_SAMPLES": str(args.query_plan_samples)},
                )
                code = result.returncode
                summary = postgres_summarize_report(report.read_text(), exit_code=code)
            except (subprocess.TimeoutExpired, OSError, ElementTree.ParseError):
                code = 1
                summary = postgres_summarize_report("<testsuites />", exit_code=code)
                summary["failure_category"] = "campaign_incomplete"
        receipt = {
            "schema": "fleet.adr006-postgres-contention/v2",
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": {"git_sha": sha, "dirty": dirty},
            "versions": {name: version(name) for name in ("mlflow", "dspy", "daytona")},
            "exclusive_database": True,
            "source": POSTGRES_TEST_PATH,
            **summary,
        }
        json.dump(receipt, destination, indent=2, sort_keys=True)
        destination.write("\n")
    complete = summary["result"]["complete_six_scenario_campaign"]
    if args.query_plans:
        plans = summary["query_plans"]
        complete = (
            complete
            and isinstance(plans, dict)
            and set(plans) == set(POSTGRES_QUERY_OPERATIONS)
            and all(entry["status"] == "passed" for entry in plans.values())
        )
    outcome = "six scenarios passed" if complete else "certification incomplete"
    print("PostgreSQL contention receipt retained; " + outcome)
    return 0 if complete else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet database setup and operator certification commands")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("upgrade", help="Apply Alembic migrations to the configured database")
    commands.add_parser("import-sqlite", help="Perform the explicit SQLite-to-PostgreSQL import")
    commands.add_parser("preflight", help="Run the sanitized Lakebase readiness preflight")
    commands.add_parser("certify-postgres", help="Run the bounded PostgreSQL contention certification")
    args, remainder = parser.parse_known_args(argv)
    dispatch = {
        "upgrade": upgrade_main,
        "import-sqlite": import_main,
        "preflight": preflight_main,
        "certify-postgres": postgres_main,
    }
    return dispatch[args.operation](remainder)


if __name__ == "__main__":
    raise SystemExit(main())
