"""Lightweight SQLite-backed local state for sessions, history, and optimization.

This module is intentionally separate from ``integrations.database``.
``integrations.database`` owns the async Neon/Postgres repository layer, while this
module owns the local SQLite sidecar used for developer workflows and lightweight
best-effort persistence.
"""

from __future__ import annotations

import asyncio
import enum
import json
import os
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Column, Integer, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Field, Session, SQLModel, create_engine, select

from fleet_rlm.integrations.database.models_enums import (
    ChatSessionStatus as DbChatSessionStatus,
)
from fleet_rlm.integrations.database.models_enums import (
    ExternalTraceProvider as DbExternalTraceProvider,
)
from fleet_rlm.integrations.database.models_enums import (
    MembershipRole as DbMembershipRole,
)
from fleet_rlm.integrations.database.models_enums import (
    MemoryScope as DbMemoryScope,
)
from fleet_rlm.integrations.database.models_enums import (
    OptimizationRunStatus as DbOptimizationRunStatus,
)
from fleet_rlm.integrations.database.models_enums import (
    RunStatus as DbRunStatus,
)
from fleet_rlm.integrations.database.models_enums import (
    TenantStatus as DbTenantStatus,
)
from fleet_rlm.integrations.database.models_memory import MemoryItem as DbMemoryItem
from fleet_rlm.integrations.database.models_optimization import (
    Dataset as DbDataset,
)
from fleet_rlm.integrations.database.models_optimization import (
    DatasetExample as DbDatasetExample,
)
from fleet_rlm.integrations.database.models_optimization import (
    EvaluationResult as DbEvaluationResult,
)
from fleet_rlm.integrations.database.models_optimization import (
    OptimizationRun as DbOptimizationRun,
)
from fleet_rlm.integrations.database.models_optimization import (
    PromptSnapshot as DbPromptSnapshot,
)
from fleet_rlm.integrations.database.models_runs import (
    Artifact as DbArtifact,
)
from fleet_rlm.integrations.database.models_runs import (
    ChatSession as DbChatSession,
)
from fleet_rlm.integrations.database.models_runs import (
    ChatTurn as DbChatTurn,
)
from fleet_rlm.integrations.database.models_runs import (
    Run as DbRun,
)
from fleet_rlm.integrations.database.models_runs import (
    RunStep as DbRunStep,
)
from fleet_rlm.integrations.database.repository_chat import (
    ArtifactCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.database.repository_memory import MemoryItemCreateRequest
from fleet_rlm.integrations.database.repository_optimization import (
    DatasetCreateRequest,
    OptimizationRunCreateRequest,
)
from fleet_rlm.integrations.persistence_protocol import PersistenceProtocol
from fleet_rlm.utils.time import utc_now as _utc_now

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
        ("metadata_json", "TEXT"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE optimization_runs ADD COLUMN {col_name} {col_type}"))
            except (OperationalError, ProgrammingError):
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
        ("model_provider", "VARCHAR(128)"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE chat_sessions ADD COLUMN {col_name} {col_type}"))
            except (OperationalError, ProgrammingError):
                pass  # column already exists
        # Best-effort index for ownership queries
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_sessions_owner "
                    "ON chat_sessions (owner_tenant, owner_user, updated_at DESC)"
                )
            )
        except (OperationalError, ProgrammingError):
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
                conn.execute(text(f"ALTER TABLE datasets ADD COLUMN {col_name} {col_type}"))
            except (OperationalError, ProgrammingError):
                pass  # column already exists
        conn.commit()


def _migrate_evaluation_tables(engine: Any) -> None:
    """Best-effort index creation for evaluation_results and prompt_snapshots.

    Tables are created by ``create_all``; this adds any post-creation indexes.
    """
    indexes = [
        (
            "ix_evaluation_results_run_index",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_results_run_index ON evaluation_results (run_id, example_index)",
        ),
        (
            "ix_prompt_snapshots_run_type",
            "CREATE INDEX IF NOT EXISTS ix_prompt_snapshots_run_type ON prompt_snapshots (run_id, prompt_type)",
        ),
    ]
    with engine.connect() as conn:
        for _name, ddl in indexes:
            try:
                conn.execute(text(ddl))
            except (OperationalError, ProgrammingError):
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
    model_provider: str | None = Field(default=None, max_length=128)
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
    expected_output: str | None = Field(default=None, description="Expected/gold output")
    predicted_output: str | None = Field(default=None, description="Model predicted output")
    score: float = Field(description="Score for this individual example (0.0-1.0)")
    created_at: datetime = Field(default_factory=_utc_now)


class PromptSnapshot(SQLModel, table=True):
    __tablename__ = "prompt_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="optimization_runs.id", index=True)
    predictor_name: str = Field(max_length=255, description="Name from named_predictors()")
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
    metadata_json: str | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)


def _parse_metadata_json(value: str | None) -> dict[str, Any]:
    """Decode persisted optimization-run metadata JSON into a dictionary."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_metadata_json(value: dict[str, Any] | None) -> str | None:
    """Encode optimization-run metadata for local SQLite persistence."""
    if not value:
        return None
    return json.dumps(value, sort_keys=True)


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
        stmt = select(ChatTurn).where(ChatTurn.session_id == session_id).order_by(text("turn_index"))
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
    metadata_json: dict[str, Any] | None = None,
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
            metadata_json=_serialize_metadata_json(metadata_json),
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
    metadata_json: dict[str, Any] | None = None,
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
        merged_metadata = _parse_metadata_json(row.metadata_json)
        if metadata_json:
            merged_metadata.update(metadata_json)
        row.metadata_json = _serialize_metadata_json(merged_metadata)
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
        stmt = select(OptimizationRun).where(OptimizationRun.status == RunStatus.RUNNING)
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
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    model_name: str | None = None,
    model_provider: str | None = None,
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
        if created_after is not None:
            base = base.where(ChatSession.created_at >= created_after)
        if created_before is not None:
            base = base.where(ChatSession.created_at <= created_before)
        if model_name is not None:
            base = base.where(ChatSession.model_name == model_name)
        if model_provider is not None:
            base = base.where(ChatSession.model_provider == model_provider)

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


def update_chat_session(
    session_id: int,
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
    title: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> ChatSession | None:
    """Update a session's title and/or metadata.

    Returns the updated session row, or None if not found or not owned.
    Local store only supports title updates; metadata_json is ignored.
    """
    with get_session() as db:
        row = db.get(ChatSession, session_id)
        if row is None:
            return None
        if owner_tenant is not None and row.owner_tenant != owner_tenant:
            return None
        if owner_user is not None and row.owner_user != owner_user:
            return None
        if title is not None:
            row.title = title
        row.updated_at = _utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
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


def restore_session(
    session_id: int,
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
) -> bool:
    """Restore an archived session by setting status to ACTIVE.

    Returns True if the session was found and restored, False otherwise.
    """
    with get_session() as db:
        row = db.get(ChatSession, session_id)
        if row is None:
            return False
        if owner_tenant is not None and row.owner_tenant != owner_tenant:
            return False
        if owner_user is not None and row.owner_user != owner_user:
            return False
        if row.status != SessionStatus.ARCHIVED:
            return False
        row.status = SessionStatus.ACTIVE
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


def get_local_session_stats(
    session_id: int,
    *,
    owner_tenant: str | None = None,
    owner_user: str | None = None,
) -> dict[str, object] | None:
    """Return aggregate stats for a local chat session."""
    with get_session() as db:
        session_row = db.get(ChatSession, session_id)
        if session_row is None:
            return None
        if owner_tenant is not None and session_row.owner_tenant != owner_tenant:
            return None
        if owner_user is not None and session_row.owner_user != owner_user:
            return None
        turns = list(
            db.exec(select(ChatTurn).where(ChatTurn.session_id == session_id).order_by(text("turn_index"))).all()
        )
    return {
        "total_tokens_in": sum(int(turn.tokens_in or 0) for turn in turns),
        "total_tokens_out": sum(int(turn.tokens_out or 0) for turn in turns),
        "total_latency_ms": sum(int(turn.latency_ms or 0) for turn in turns),
        "model_breakdown": ({session_row.model_name: len(turns)} if session_row.model_name else {}),
    }


# ---------------------------------------------------------------------------
# Evaluation result + prompt snapshot persistence
# ---------------------------------------------------------------------------


def save_evaluation_results(
    run_id: int,
    results: list[dict],
) -> list[EvaluationResult]:
    """Bulk save per-example evaluation results for an optimization run."""
    with get_session() as db:
        existing_rows = list(
            db.exec(select(EvaluationResult).where(cast(Any, EvaluationResult.run_id) == run_id)).all()
        )
        for existing in existing_rows:
            db.delete(existing)
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
        existing_rows = list(db.exec(select(PromptSnapshot).where(cast(Any, PromptSnapshot.run_id) == run_id)).all())
        for existing in existing_rows:
            db.delete(existing)
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
    from fleet_rlm.quality.transcript_exports import (
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


def _uuid_to_int(value: uuid.UUID | None) -> int | None:
    """Convert a UUID to an integer for local-store primary-key lookup.

    Local store uses auto-incrementing integer IDs.  When the protocol
    passes a UUID that was created from an integer (``uuid.UUID(int=x)``)
    we can recover the original value via ``value.int``.
    """
    if value is None:
        return None
    if value.int > 2**63 - 1:
        return None
    return value.int


class LocalStore(PersistenceProtocol):
    """SQLite-backed local persistence implementing the unified protocol.

    This is a best-effort adapter that delegates to the module-level
    synchronous local-store functions.  Methods that have no local-store
    equivalent return sensible defaults or raise ``NotImplementedError``.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def upsert_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
    ) -> IdentityUpsertResult:
        # Derive deterministic UUIDs from claims so ownership lookups work
        # consistently across the local-store backend.
        _LOCAL_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        tenant_id = uuid.uuid5(_LOCAL_NS, f"tenant:{entra_tenant_id}")
        user_id = uuid.uuid5(_LOCAL_NS, f"user:{entra_user_id}@{entra_tenant_id}")
        workspace_id = uuid.uuid5(_LOCAL_NS, f"workspace:{entra_tenant_id}")
        return IdentityUpsertResult(
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_status=DbTenantStatus.ACTIVE,
            membership_role=DbMembershipRole.MEMBER,
            workspace_id=workspace_id,
        )

    async def resolve_workspace_id(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return workspace_id or tenant_id

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def list_chat_sessions(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        search: str | None = None,
        status: DbChatSessionStatus | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DbChatSession], int]:
        status_filter = SessionStatus(status.value) if status is not None else None
        items, total = await asyncio.to_thread(
            list_sessions,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
            search=search,
            status=status_filter,
            created_after=created_after,
            created_before=created_before,
            model_name=model_name,
            model_provider=model_provider,
            limit=limit,
            offset=offset,
        )
        return cast(tuple[list[DbChatSession], int], (items, total))

    async def get_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> DbChatSession | None:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return None
        result = await asyncio.to_thread(
            get_chat_session,
            session_id_int,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
        )
        return cast(DbChatSession | None, result)

    async def list_chat_turns(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DbChatTurn], int]:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return [], 0
        items, total = await asyncio.to_thread(
            get_turns_paginated,
            session_id_int,
            limit=limit,
            offset=offset,
        )
        return cast(tuple[list[DbChatTurn], int], (items, total))

    async def update_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        title: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DbChatSession | None:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return None
        result = await asyncio.to_thread(
            update_chat_session,
            session_id_int,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
            title=title,
            metadata_json=metadata_json,
        )
        return cast(DbChatSession | None, result)

    async def archive_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return False
        return await asyncio.to_thread(
            archive_session,
            session_id_int,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
        )

    async def restore_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return False
        return await asyncio.to_thread(
            restore_session,
            session_id_int,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
        )

    async def get_session_stats(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, object] | None:
        session_id_int = _uuid_to_int(session_id)
        if session_id_int is None:
            return None
        return await asyncio.to_thread(
            get_local_session_stats,
            session_id_int,
            owner_tenant=str(tenant_id),
            owner_user=str(user_id) if user_id is not None else None,
        )

    # ------------------------------------------------------------------
    # Runs / Steps
    # ------------------------------------------------------------------

    async def create_run(self, request: RunCreateRequest) -> DbRun:
        raise NotImplementedError("LocalStore does not support run creation")

    async def get_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbRun | None:
        return None

    async def get_run_steps_paginated(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DbRunStep], int]:
        return [], 0

    async def append_step(self, request: RunStepCreateRequest) -> DbRunStep:
        raise NotImplementedError("LocalStore does not support step append")

    async def update_run_status(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        status: DbRunStatus,
        error_json: dict | None = None,
    ) -> DbRun | None:
        return None

    async def store_artifact(self, request: ArtifactCreateRequest) -> DbArtifact:
        raise NotImplementedError("LocalStore does not support artifact storage")

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    async def store_memory_item(self, request: MemoryItemCreateRequest) -> DbMemoryItem:
        raise NotImplementedError("LocalStore does not support memory items")

    async def list_memory_items_paginated(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        scope: DbMemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DbMemoryItem], int]:
        return [], 0

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    async def store_trace_feedback(
        self,
        *,
        tenant_id: uuid.UUID,
        trace_id: str,
        is_correct: bool,
        workspace_id: uuid.UUID | None = None,
        reviewer_user_id: uuid.UUID | None = None,
        comment: str | None = None,
        expected_response: str | None = None,
        provider: DbExternalTraceProvider = DbExternalTraceProvider.MLFLOW,
        client_request_id: str | None = None,
        run_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        return uuid.UUID(int=0)

    async def store_rlm_trace(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        trace_id: str,
        workspace_id: uuid.UUID | None = None,
        run_step_id: uuid.UUID | None = None,
        summary_text: str | None = None,
        payload_json: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> uuid.UUID:
        return uuid.UUID(int=0)

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    async def create_dataset(
        self,
        request: DatasetCreateRequest,
        *,
        examples: Sequence[dict[str, Any]] | None = None,
    ) -> DbDataset:
        result = await asyncio.to_thread(
            create_dataset,
            name=request.name,
            row_count=request.row_count,
            format=str(request.format.value) if request.format is not None else "jsonl",
            uri=request.uri or "",
            module_slug=request.module_slug,
        )
        return cast(DbDataset, result)

    async def list_datasets(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        module_slug: str | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DbDataset], int]:
        items, total = await asyncio.to_thread(
            list_datasets,
            module_slug=module_slug,
            limit=limit,
            offset=offset,
        )
        return cast(tuple[list[DbDataset], int], (items, total))

    async def get_dataset(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbDataset | None:
        dataset_id_int = _uuid_to_int(dataset_id)
        if dataset_id_int is None:
            return None
        result = await asyncio.to_thread(get_dataset, dataset_id_int)
        return cast(DbDataset | None, result)

    async def list_dataset_examples(
        self,
        *,
        tenant_id: uuid.UUID,
        dataset_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[DbDatasetExample], int]:
        return [], 0

    # ------------------------------------------------------------------
    # Optimization runs
    # ------------------------------------------------------------------

    async def create_optimization_run(
        self,
        request: OptimizationRunCreateRequest,
    ) -> DbOptimizationRun:
        dataset_id_int = _uuid_to_int(request.dataset_id)
        optimizer = OptimizerType.GEPA
        try:
            optimizer = OptimizerType(request.optimizer)
        except ValueError:
            pass
        result = await asyncio.to_thread(
            create_optimization_run,
            program_spec=request.program_spec,
            optimizer=optimizer,
            dataset_id=dataset_id_int,
            auto=request.auto or "light",
            train_ratio=request.train_ratio,
            module_slug=request.module_slug,
            dataset_path=request.output_path,
            metadata_json=dict(request.metadata_json),
        )
        return cast(DbOptimizationRun, result)

    async def list_optimization_runs(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        status: DbOptimizationRunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DbOptimizationRun]:
        local_status = None
        if status is not None:
            local_status = RunStatus(status.value)
        items = await asyncio.to_thread(
            list_optimization_runs,
            status=local_status,
            limit=limit,
            offset=offset,
        )
        return cast(list[DbOptimizationRun], items)

    async def get_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbOptimizationRun | None:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return None
        result = await asyncio.to_thread(get_optimization_run, run_id_int)
        return cast(DbOptimizationRun | None, result)

    async def update_optimization_run_phase(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        phase: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbOptimizationRun | None:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return None
        await asyncio.to_thread(
            update_optimization_run_phase,
            run_id_int,
            phase=phase,
        )
        result = await asyncio.to_thread(get_optimization_run, run_id_int)
        return cast(DbOptimizationRun | None, result)

    async def complete_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        train_examples: int,
        validation_examples: int,
        validation_score: float | None = None,
        output_path: str | None = None,
        manifest_path: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbOptimizationRun | None:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return None
        result = await asyncio.to_thread(
            complete_optimization_run,
            run_id_int,
            train_examples=train_examples,
            validation_examples=validation_examples,
            validation_score=validation_score,
            output_path=output_path,
            manifest_path=manifest_path,
            metadata_json=metadata_json,
        )
        return cast(DbOptimizationRun | None, result)

    async def fail_optimization_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        error: str,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> DbOptimizationRun | None:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return None
        result = await asyncio.to_thread(
            fail_optimization_run,
            run_id_int,
            error=error,
        )
        return cast(DbOptimizationRun | None, result)

    async def recover_stale_optimization_runs(self) -> int:
        return await asyncio.to_thread(recover_stale_optimization_runs)

    async def save_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        results: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[DbEvaluationResult]:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return []
        items = await asyncio.to_thread(
            save_evaluation_results,
            run_id_int,
            list(results),
        )
        return cast(list[DbEvaluationResult], items)

    async def get_evaluation_results(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DbEvaluationResult], int]:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return [], 0
        items, total = await asyncio.to_thread(
            get_evaluation_results,
            run_id_int,
            limit=limit,
            offset=offset,
        )
        return cast(tuple[list[DbEvaluationResult], int], (items, total))

    async def save_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        snapshots: Sequence[dict[str, Any]],
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[DbPromptSnapshot]:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return []
        items = await asyncio.to_thread(
            save_prompt_snapshots,
            run_id_int,
            list(snapshots),
        )
        return cast(list[DbPromptSnapshot], items)

    async def get_prompt_snapshots(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> list[DbPromptSnapshot]:
        run_id_int = _uuid_to_int(run_id)
        if run_id_int is None:
            return []
        items = await asyncio.to_thread(get_prompt_snapshots, run_id_int)
        return cast(list[DbPromptSnapshot], items)
