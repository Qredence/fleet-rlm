"""Lightweight SQLite-backed local state for sessions, history, and optimization.

This module is intentionally separate from ``integrations.database``.
``integrations.database`` owns the async Neon/Postgres repository layer, while this
module owns the local SQLite sidecar used for developer workflows and lightweight
best-effort persistence.
"""

from __future__ import annotations

import enum
import json
import os
import tempfile
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


def _migrate_chat_sessions(engine: Any) -> None:
    """Best-effort migration for ownership + external ID columns on chat_sessions."""
    new_columns = [
        ("external_session_id", "VARCHAR(255)"),
        ("owner_tenant", "VARCHAR(255)"),
        ("owner_user", "VARCHAR(255)"),
        ("workspace_id", "VARCHAR(255)"),
        ("_monotonic_turn_counter", "INTEGER DEFAULT 0 NOT NULL"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(
                    text(f"ALTER TABLE chat_sessions ADD COLUMN {col_name} {col_type}")
                )
            except Exception:
                pass  # column already exists
        # Best-effort index for ownership queries
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_sessions_owner "
                    "ON chat_sessions (owner_tenant, owner_user, updated_at DESC)"
                )
            )
        except Exception:
            # This index is an opportunistic local-store optimization; startup should
            # not fail if a legacy SQLite version or partial schema cannot create it.
            pass
        conn.commit()


def _migrate_dataset_columns(engine: Any) -> None:
    """Best-effort migration for new columns on the datasets table."""
    new_columns = [
        ("format", "VARCHAR(16)"),
        ("module_slug", "VARCHAR(128)"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(
                    text(f"ALTER TABLE datasets ADD COLUMN {col_name} {col_type}")
                )
            except Exception:
                pass  # column already exists
        conn.commit()


def _migrate_evaluation_tables(engine: Any) -> None:
    """Best-effort index creation for evaluation_results and prompt_snapshots.

    Tables are created by ``create_all``; this adds any post-creation indexes.
    """
    indexes = [
        (
            "ix_evaluation_results_run_index",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_results_run_index "
            "ON evaluation_results (run_id, example_index)",
        ),
        (
            "ix_prompt_snapshots_run_type",
            "CREATE INDEX IF NOT EXISTS ix_prompt_snapshots_run_type "
            "ON prompt_snapshots (run_id, prompt_type)",
        ),
    ]
    with engine.connect() as conn:
        for _name, ddl in indexes:
            try:
                conn.execute(text(ddl))
            except Exception:
                # Evaluation tables are best-effort local developer persistence; index
                # creation failure should not block the app from serving requests.
                pass
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
    _migrate_chat_sessions(engine)
    _migrate_dataset_columns(engine)
    _migrate_evaluation_tables(engine)
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
    external_session_id: str | None = Field(default=None, max_length=255, index=True)
    owner_tenant: str | None = Field(default=None, max_length=255)
    owner_user: str | None = Field(default=None, max_length=255)
    workspace_id: str | None = Field(default=None, max_length=255)
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
    format: str | None = Field(default=None, max_length=16)
    module_slug: str | None = Field(default=None, max_length=128)
    input_keys: str | None = None
    output_key: str = Field(default="assistant_response", max_length=128)
    created_at: datetime = Field(default_factory=_utc_now)


class EvaluationResult(SQLModel, table=True):
    __tablename__ = "evaluation_results"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="optimization_runs.id", index=True)
    example_index: int = Field(description="Zero-based index in the evaluation dataset")
    input_data: str = Field(description="JSON-serialized input fields for this example")
    expected_output: str | None = Field(
        default=None, description="Expected/gold output"
    )
    predicted_output: str | None = Field(
        default=None, description="Model predicted output"
    )
    score: float = Field(description="Score for this individual example (0.0-1.0)")
    created_at: datetime = Field(default_factory=_utc_now)


class PromptSnapshot(SQLModel, table=True):
    __tablename__ = "prompt_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="optimization_runs.id", index=True)
    predictor_name: str = Field(
        max_length=255, description="Name from named_predictors()"
    )
    prompt_type: str = Field(max_length=16, description="'before' or 'after'")
    prompt_text: str = Field(description="Full prompt/instruction text")
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
    *,
    title: str = "New Session",
    model_name: str | None = None,
    external_session_id: str | None = None,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
    workspace_id: str | None = None,
) -> ChatSession:
    with get_session() as db:
        row = ChatSession(
            title=title,
            model_name=model_name,
            external_session_id=external_session_id,
            owner_tenant=owner_tenant,
            owner_user=owner_user,
            workspace_id=workspace_id,
        )
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


# ---------------------------------------------------------------------------
# Session history queries
# ---------------------------------------------------------------------------


def list_sessions(
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
    search: str | None = None,
    status: SessionStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ChatSession], int]:
    """Return (items, total_count) for paginated session listing.

    Filters by owner when provided, with full-text search on title and
    external_session_id.
    """
    with get_session() as db:
        base = select(ChatSession)
        if owner_tenant is not None:
            base = base.where(ChatSession.owner_tenant == owner_tenant)
        if owner_user is not None:
            base = base.where(ChatSession.owner_user == owner_user)
        if status is not None:
            base = base.where(ChatSession.status == status)
        else:
            base = base.where(ChatSession.status == SessionStatus.ACTIVE)
        if search:
            like_pat = f"%{search}%"
            base = base.where(
                (ChatSession.title.contains(search))  # type: ignore
                | (ChatSession.external_session_id.like(like_pat))  # type: ignore
            )

        from sqlalchemy import func

        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.exec(count_stmt).one()

        items_stmt = (
            base.order_by(ChatSession.updated_at.desc()).offset(offset).limit(limit)  # type: ignore
        )
        items = list(db.exec(items_stmt).all())
        return items, total


def get_chat_session(
    session_id: int,
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
) -> ChatSession | None:
    """Return a session by ID with ownership check.

    Returns None if the session does not exist or does not belong to the caller.
    """
    with get_session() as db:
        row = db.get(ChatSession, session_id)
        if row is None:
            return None
        if owner_tenant is not None and row.owner_tenant != owner_tenant:
            return None
        if owner_user is not None and row.owner_user != owner_user:
            return None
        return row


def archive_session(
    session_id: int,
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
) -> bool:
    """Soft-delete a session by setting status to ARCHIVED.

    Returns True if the session was found and archived, False otherwise.
    """
    with get_session() as db:
        row = db.get(ChatSession, session_id)
        if row is None:
            return False
        if owner_tenant is not None and row.owner_tenant != owner_tenant:
            return False
        if owner_user is not None and row.owner_user != owner_user:
            return False
        row.status = SessionStatus.ARCHIVED
        row.updated_at = _utc_now()
        db.add(row)
        db.commit()
        return True


def get_turns_paginated(
    session_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ChatTurn], int]:
    """Return (items, total_count) for paginated turn listing."""
    with get_session() as db:
        from sqlalchemy import func

        count_stmt = select(func.count()).where(ChatTurn.session_id == session_id)
        total = db.exec(count_stmt).one()

        items_stmt = (
            select(ChatTurn)
            .where(ChatTurn.session_id == session_id)
            .order_by(text("turn_index"))
            .offset(offset)
            .limit(limit)
        )
        items = list(db.exec(items_stmt).all())
        return items, total


# ---------------------------------------------------------------------------
# Evaluation result + prompt snapshot persistence
# ---------------------------------------------------------------------------


def save_evaluation_results(
    run_id: int,
    results: list[dict],
) -> list[EvaluationResult]:
    """Bulk save per-example evaluation results for an optimization run."""
    with get_session() as db:
        rows: list[EvaluationResult] = []
        for r in results:
            row = EvaluationResult(
                run_id=run_id,
                example_index=r["example_index"],
                input_data=r["input_data"],
                expected_output=r.get("expected_output"),
                predicted_output=r.get("predicted_output"),
                score=r["score"],
            )
            db.add(row)
            rows.append(row)
        db.commit()
        for row in rows:
            db.refresh(row)
        return rows


def get_evaluation_results(
    run_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[EvaluationResult], int]:
    """Return (items, total_count) for paginated evaluation results."""
    with get_session() as db:
        from sqlalchemy import func

        count_stmt = select(func.count()).where(EvaluationResult.run_id == run_id)
        total = db.exec(count_stmt).one()

        items_stmt = (
            select(EvaluationResult)
            .where(EvaluationResult.run_id == run_id)
            .order_by(EvaluationResult.example_index)  # type: ignore
            .offset(offset)
            .limit(limit)
        )
        items = list(db.exec(items_stmt).all())
        return items, total


def save_prompt_snapshots(
    run_id: int,
    snapshots: list[dict],
) -> list[PromptSnapshot]:
    """Bulk save before/after prompt snapshots for an optimization run."""
    with get_session() as db:
        rows: list[PromptSnapshot] = []
        for s in snapshots:
            row = PromptSnapshot(
                run_id=run_id,
                predictor_name=s["predictor_name"],
                prompt_type=s["prompt_type"],
                prompt_text=s["prompt_text"],
            )
            db.add(row)
            rows.append(row)
        db.commit()
        for row in rows:
            db.refresh(row)
        return rows


def get_prompt_snapshots(
    run_id: int,
) -> list[PromptSnapshot]:
    """Return all prompt snapshots for a run (typically small: 2 per predictor)."""
    with get_session() as db:
        stmt = (
            select(PromptSnapshot)
            .where(PromptSnapshot.run_id == run_id)
            .order_by(PromptSnapshot.predictor_name, PromptSnapshot.prompt_type)
        )
        return list(db.exec(stmt).all())


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


def get_dataset_root() -> Path:
    """Return the dataset storage root directory, creating it if needed."""
    env = os.environ.get("FLEET_RLM_DATASET_ROOT")
    if env:
        root = Path(env).resolve()
    else:
        root = (_DEFAULT_DB_DIR / "datasets").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_dataset(
    *,
    name: str,
    row_count: int,
    format: str,
    uri: str,
    module_slug: str | None = None,
) -> Dataset:
    """Create a new dataset record."""
    with get_session() as db:
        row = Dataset(
            name=name,
            row_count=row_count,
            format=format,
            uri=uri,
            module_slug=module_slug,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def list_datasets(
    *,
    module_slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Dataset], int]:
    """Paginated dataset listing with optional module filter."""
    with get_session() as db:
        from sqlalchemy import func

        base = select(Dataset)
        if module_slug is not None:
            base = base.where(Dataset.module_slug == module_slug)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = db.exec(count_stmt).one()

        items_stmt = (
            base.order_by(Dataset.created_at.desc())  # type: ignore
            .offset(offset)
            .limit(limit)
        )
        items = list(db.exec(items_stmt).all())
        return items, total


def get_dataset(dataset_id: int) -> Dataset | None:
    """Return a single dataset by ID."""
    with get_session() as db:
        return db.get(Dataset, dataset_id)


def _build_transcript_dataset_rows(
    *,
    module_slug: str,
    turns: list[tuple[str | None, str | None]],
) -> tuple[list[dict[str, object]], str]:
    """Map transcript turns into module-specific dataset rows."""
    from fleet_rlm.runtime.quality.transcript_exports import (
        build_transcript_dataset_rows,
    )

    return build_transcript_dataset_rows(module_slug=module_slug, turns=turns)


def _persist_transcript_dataset(
    *,
    rows: list[dict[str, object]],
    module_slug: str,
    dataset_name: str,
    filename_stem: str,
) -> Dataset:
    """Write transcript-derived rows to JSONL and register a dataset."""
    _ = filename_stem  # Human-readable naming stays in dataset metadata, not file paths.
    root = get_dataset_root().resolve()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=root,
        prefix="transcript-",
        suffix=".jsonl",
        delete=False,
    ) as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        dest = Path(fh.name)

    return create_dataset(
        name=dataset_name,
        row_count=len(rows),
        format="jsonl",
        uri=str(dest),
        module_slug=module_slug,
    )


def create_transcript_dataset(
    *,
    module_slug: str,
    turns: list[tuple[str | None, str | None]],
    title: str | None = None,
) -> Dataset:
    """Convert transcript turns into a JSONL dataset for GEPA optimization."""
    rows, label = _build_transcript_dataset_rows(
        module_slug=module_slug,
        turns=turns,
    )
    transcript_title = title.strip() if title else "Transcript"
    return _persist_transcript_dataset(
        rows=rows,
        module_slug=module_slug,
        dataset_name=f"{transcript_title} ({label})",
        filename_stem=transcript_title,
    )


def export_session_as_dataset(session_id: int, module_slug: str) -> Dataset:
    """Convert a session's turns into a JSONL dataset for GEPA optimization."""
    turns = get_turns(session_id)
    return create_transcript_dataset(
        module_slug=module_slug,
        turns=[(turn.user_message, turn.assistant_message) for turn in turns],
        title=f"Session {session_id}",
    )
