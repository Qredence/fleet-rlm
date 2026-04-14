"""Lightweight SQLite-backed local state for sessions, history, and optimization.

This module is intentionally separate from ``integrations.database``.
``integrations.database`` owns the async Neon/Postgres repository layer, while this
module owns the local SQLite sidecar used for developer workflows and lightweight
best-effort persistence.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, text
from sqlmodel import Field, Session, SQLModel, create_engine, select

_DEFAULT_DB_DIR = Path(".data")
_engines: dict[str, Any] = {}


def _iter_cached_engines() -> Iterator[Any]:
    """Yield cached engines for tests and maintenance."""
    return iter(_engines.values())


def _resolve_db_url(db_path: str | None = None) -> str:
    """Resolve the effective database URL for the requested local store."""
    env_url = os.environ.get("FLEET_RLM_LOCAL_DB_URL")
    if env_url:
        return env_url

    path = (Path(db_path) if db_path else _DEFAULT_DB_DIR / "local.db").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.resolve()}"


def _migrate_optimization_runs(engine: Any) -> None:
    """Best-effort migration for new columns on the optimization_runs table.

    SQLite ``CREATE TABLE IF NOT EXISTS`` (via ``create_all``) does not add
    columns to an existing table.  We use ``ALTER TABLE ADD COLUMN`` wrapped
    in try/except so it is safe to run repeatedly.
    """
    new_columns = [
        ("module_slug", "VARCHAR(128)"),
        ("dataset_path", "TEXT"),
        ("manifest_path", "TEXT"),
        ("phase", "VARCHAR(64)"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(
                    text(
                        f"ALTER TABLE optimization_runs ADD COLUMN {col_name} {col_type}"
                    )
                )
            except Exception:
                pass  # column already exists
        conn.commit()


def get_engine(db_path: str | None = None):
    """Return a cached SQLite engine, creating the DB file + tables on first call."""
    url = _resolve_db_url(db_path)
    engine = _engines.get(url)
    if engine is not None:
        return engine

    engine = create_engine(url, echo=False)
    SQLModel.metadata.create_all(engine)
    _migrate_optimization_runs(engine)
    _engines[url] = engine
    return engine


def get_session(db_path: str | None = None) -> Session:
    """Return a new SQLModel session bound to the local SQLite engine."""
    return Session(get_engine(db_path))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class OptimizerType(str, enum.Enum):
    GEPA = "gepa"
    MIPROV2 = "miprov2"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="New Session", max_length=255)
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    model_name: str | None = Field(default=None, max_length=255)
    monotonic_turn_counter: int = Field(
        default=0,
        sa_column=Column("_monotonic_turn_counter", Integer, default=0, nullable=False),
    )
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ChatTurn(SQLModel, table=True):
    __tablename__ = "chat_turns"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chat_sessions.id", index=True)
    turn_index: int
    user_message: str
    assistant_message: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, index=True)
    uri: str
    row_count: int | None = None
    input_keys: str | None = None
    output_key: str = Field(default="assistant_response", max_length=128)
    created_at: datetime = Field(default_factory=_utc_now)


class OptimizationRun(SQLModel, table=True):
    __tablename__ = "optimization_runs"

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int | None = Field(default=None, foreign_key="datasets.id")
    optimizer: OptimizerType
    status: RunStatus = Field(default=RunStatus.RUNNING)
    program_spec: str = Field(max_length=255)
    output_path: str | None = None
    auto: str | None = Field(default="light", max_length=16)
    train_ratio: float = Field(default=0.8)
    train_examples: int | None = None
    validation_examples: int | None = None
    validation_score: float | None = None
    error: str | None = None
    module_slug: str | None = Field(default=None, max_length=128)
    dataset_path: str | None = None
    manifest_path: str | None = None
    phase: str | None = Field(default=None, max_length=64)
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)


def create_session(
    *, title: str = "New Session", model_name: str | None = None
) -> ChatSession:
    with get_session() as db:
        row = ChatSession(title=title, model_name=model_name)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def add_turn(
    session_id: int,
    turn_index: int,
    user_message: str,
    assistant_message: str | None = None,
    **kwargs: Any,
) -> ChatTurn:
    with get_session() as db:
        session_row = db.get(ChatSession, session_id)
        if session_row is None:
            raise ValueError(f"ChatSession with id {session_id} not found")
        monotonic_index = session_row.monotonic_turn_counter
        row = ChatTurn(
            session_id=session_id,
            turn_index=monotonic_index,
            user_message=user_message,
            assistant_message=assistant_message,
            **kwargs,
        )
        db.add(row)
        session_row.monotonic_turn_counter = monotonic_index + 1
        session_row.updated_at = _utc_now()
        db.add(session_row)
        db.commit()
        db.refresh(row)
        return row


def get_turns(session_id: int) -> list[ChatTurn]:
    with get_session() as db:
        stmt = (
            select(ChatTurn)
            .where(ChatTurn.session_id == session_id)
            .order_by(text("turn_index"))
        )
        return list(db.exec(stmt).all())


def register_dataset(
    name: str,
    uri: str,
    *,
    row_count: int | None = None,
    input_keys: list[str] | None = None,
    output_key: str = "assistant_response",
) -> Dataset:
    with get_session() as db:
        row = Dataset(
            name=name,
            uri=uri,
            row_count=row_count,
            input_keys=",".join(input_keys) if input_keys else None,
            output_key=output_key,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def create_optimization_run(
    *,
    program_spec: str,
    optimizer: OptimizerType = OptimizerType.GEPA,
    dataset_id: int | None = None,
    auto: str = "light",
    train_ratio: float = 0.8,
    module_slug: str | None = None,
    dataset_path: str | None = None,
) -> OptimizationRun:
    with get_session() as db:
        row = OptimizationRun(
            program_spec=program_spec,
            optimizer=optimizer,
            dataset_id=dataset_id,
            auto=auto,
            train_ratio=train_ratio,
            module_slug=module_slug,
            dataset_path=dataset_path,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def complete_optimization_run(
    run_id: int,
    *,
    train_examples: int,
    validation_examples: int,
    validation_score: float | None = None,
    output_path: str | None = None,
    manifest_path: str | None = None,
) -> OptimizationRun | None:
    with get_session() as db:
        row = db.get(OptimizationRun, run_id)
        if row is None:
            return None
        row.status = RunStatus.COMPLETED
        row.train_examples = train_examples
        row.validation_examples = validation_examples
        row.validation_score = validation_score
        row.output_path = output_path
        row.manifest_path = manifest_path
        row.phase = "completed"
        row.completed_at = _utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def fail_optimization_run(run_id: int, *, error: str) -> OptimizationRun | None:
    with get_session() as db:
        row = db.get(OptimizationRun, run_id)
        if row is None:
            return None
        row.status = RunStatus.FAILED
        row.error = error
        row.phase = "failed"
        row.completed_at = _utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def get_optimization_run(run_id: int) -> OptimizationRun | None:
    """Return a single optimization run by primary key."""
    with get_session() as db:
        return db.get(OptimizationRun, run_id)


def list_optimization_runs(
    *,
    status: RunStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OptimizationRun]:
    """Return optimization runs ordered by most-recent first."""
    with get_session() as db:
        stmt = select(OptimizationRun).order_by(
            OptimizationRun.created_at.desc()  # type: ignore
        )
        if status is not None:
            stmt = stmt.where(OptimizationRun.status == status)
        stmt = stmt.offset(offset).limit(limit)
        return list(db.exec(stmt).all())


def update_optimization_run_phase(run_id: int, *, phase: str) -> None:
    """Update the current phase of a running optimization."""
    with get_session() as db:
        row = db.get(OptimizationRun, run_id)
        if row is None:
            return
        row.phase = phase
        db.add(row)
        db.commit()


def recover_stale_optimization_runs() -> int:
    """Mark any RUNNING rows as FAILED on startup (server restart recovery).

    Returns the number of rows recovered.
    """
    with get_session() as db:
        stmt = select(OptimizationRun).where(
            OptimizationRun.status == RunStatus.RUNNING
        )
        stale = list(db.exec(stmt).all())
        for row in stale:
            row.status = RunStatus.FAILED
            row.error = "Server restarted while optimization was in progress"
            row.phase = "failed"
            row.completed_at = _utc_now()
            db.add(row)
        if stale:
            db.commit()
        return len(stale)
