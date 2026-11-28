"""Postgres models and short, session-scoped repository operations."""

from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal, TypedDict
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    case,
    cast,
    delete,
    func,
    literal,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH, TSQUERY, TSVECTOR, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from fleet_rlm.config import Settings

_ROOT_LOCK_INDEX = "uq_runs_one_running_root_per_session"
_RUNNING_ROOT = text("parent_run_id IS NULL AND status = 'running'")
_OPT_JOB_LOCK_INDEX = "uq_optimization_jobs_one_active_per_target"
_ACTIVE_OPT_JOB = text("status IN ('queued', 'running')")
_OPT_PROMOTION_LOCK_INDEX = "uq_optimization_promotions_one_active_per_target"
_ACTIVE_OPT_PROMOTION = text("active = true")
_LLM_PROVIDER_NAME_CONSTRAINT = "uq_llm_providers_name"
_OPT_DATASET_NAME_CONSTRAINT = "uq_optimization_datasets_name"
_UPLOAD_LEASE = timedelta(minutes=5)
_UNSET: Any = object()


# Mutations lock the Session before its root Run. Allow foreign-key checks by child inserts.
SESSION_WRITE_LOCK: dict[str, Any] = {"key_share": True}

type SessionFactory = async_sessionmaker[AsyncSession]
type JsonObject = dict[str, Any]
type TerminalStatus = Literal["completed", "failed", "cancelled"]


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    title: Mapped[str] = mapped_column(Text)
    daytona_sandbox_id: Mapped[str | None] = mapped_column(Text, unique=True)
    pending_sandbox_name: Mapped[str | None] = mapped_column(Text)
    sandbox_needs_cleanup: Mapped[bool] = mapped_column(server_default=text("false"))
    deleting: Mapped[bool] = mapped_column(server_default=text("false"))
    upload_key: Mapped[str | None] = mapped_column(Text)
    upload_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_json: Mapped[JsonObject] = mapped_column(
        JSONB, server_default=text("""'{"messages": []}'::jsonb""")
    )
    model_override: Mapped[str | None] = mapped_column(Text)
    reasoning_override: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("depth BETWEEN 0 AND 8", name="ck_runs_depth"),
        CheckConstraint("kind IN ('root', 'subagent', 'llm_query')", name="ck_runs_kind"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')", name="ck_runs_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    root_run_id: Mapped[UUID]
    depth: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    ui_parts: Mapped[list[JsonObject]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(_ROOT_LOCK_INDEX, Run.session_id, unique=True, postgresql_where=_RUNNING_ROOT)
Index("idx_runs_root_tree", Run.root_run_id, Run.id)
Index(
    "idx_runs_session_roots",
    Run.session_id,
    Run.id.desc(),
    postgresql_where=Run.parent_run_id.is_(None),
)
Index("idx_runs_session_id", Run.session_id)
Index(
    "idx_runs_parent_run_id",
    Run.parent_run_id,
    postgresql_where=Run.parent_run_id.is_not(None),
)


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("session_id", "path", name="uq_files_session_path"),
        CheckConstraint("kind IN ('input', 'artifact')", name="ck_files_kind"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StagedFile(TypedDict):
    path: str
    object_key: str
    sha256: str
    size_bytes: int
    kind: Literal["input", "artifact"]


class ObjectCleanup(Base):
    """An uncommitted multipart write or an object retired by a manifest change."""

    __tablename__ = "workspace_object_cleanup"

    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    upload_id: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[UUID | None] = mapped_column()


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("source_run_id", name="uq_memories_source_run_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", name="fk_memories_source_run_id", ondelete="CASCADE")
    )
    skill_name: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(server_default=text("false"))
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("idx_memories_search_tsv", Memory.search_tsv, postgresql_using="gin")
Index("idx_memories_session_id", Memory.session_id)


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_workspace_settings_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    overrides_json: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


type OptimizationTarget = Literal["rlm", "skills", "memory"]
type OptimizationDatasetSource = Literal["builtin", "custom", "db"]
type OptimizationStorageBackend = Literal["neon", "daytona_volume"]
type OptimizationJobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
type LLMProviderDialect = Literal[
    "openai",
    "openai_compatible",
    "anthropic",
    "gemini",
    "openrouter",
    "xai",
    "groq",
    "deepseek",
    "ollama",
    "vllm",
    "typesafe",
]
type LLMProviderApiMode = Literal["auto", "chat", "responses"]


class LLMProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_llm_providers_name_nonempty"),
        CheckConstraint(
            "dialect IN ('openai', 'openai_compatible', 'anthropic', 'gemini', "
            "'openrouter', 'xai', 'groq', 'deepseek', 'ollama', 'vllm', 'typesafe')",
            name="ck_llm_providers_dialect",
        ),
        CheckConstraint(
            "api_mode IN ('auto', 'chat', 'responses')",
            name="ck_llm_providers_api_mode",
        ),
        UniqueConstraint("name", name=_LLM_PROVIDER_NAME_CONSTRAINT),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    name: Mapped[str] = mapped_column(Text)
    dialect: Mapped[str] = mapped_column(Text)
    api_mode: Mapped[str] = mapped_column(Text, server_default=text("'auto'"))
    base_url: Mapped[str | None] = mapped_column(Text)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(Text)
    default_model: Mapped[str | None] = mapped_column(Text)
    cached_models_json: Mapped[list[JsonObject] | None] = mapped_column(JSONB)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OptimizationDataset(Base):
    __tablename__ = "optimization_datasets"
    __table_args__ = (
        CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')", name="ck_optimization_datasets_target"
        ),
        CheckConstraint(
            "storage_backend IN ('neon', 'daytona_volume')",
            name="ck_optimization_datasets_storage_backend",
        ),
        UniqueConstraint("name", name=_OPT_DATASET_NAME_CONSTRAINT),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    name: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    storage_backend: Mapped[str] = mapped_column(Text, server_default=text("'neon'"))
    storage_volume: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    summary_json: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OptimizationJob(Base):
    __tablename__ = "optimization_jobs"
    __table_args__ = (
        CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')", name="ck_optimization_jobs_target"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_optimization_jobs_status",
        ),
        CheckConstraint(
            "dataset_source IN ('builtin', 'custom', 'db')",
            name="ck_optimization_jobs_dataset_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    target: Mapped[str] = mapped_column(Text)
    program_kind: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    stage: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    dataset_source: Mapped[str] = mapped_column(Text)
    dataset_name: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("optimization_datasets.id", ondelete="SET NULL")
    )
    decision_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "llm_providers.id",
            name="fk_optimization_jobs_decision_provider_id",
            ondelete="SET NULL",
        )
    )
    config_json: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    progress_json: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    report_json: Mapped[JsonObject | None] = mapped_column(JSONB)
    log_tail: Mapped[str] = mapped_column(Text, server_default=text("''"))
    saved: Mapped[bool | None] = mapped_column(Boolean)
    baseline_score: Mapped[float | None] = mapped_column(Float)
    optimized_score: Mapped[float | None] = mapped_column(Float)
    train_size: Mapped[int | None] = mapped_column(Integer)
    val_size: Mapped[int | None] = mapped_column(Integer)
    holdout_size: Mapped[int | None] = mapped_column(Integer)
    artifact_object_key: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    resolved_decision_model: Mapped[str | None] = mapped_column(Text)
    mlflow_run_id: Mapped[str | None] = mapped_column(Text)
    mlflow_experiment: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(_OPT_JOB_LOCK_INDEX, OptimizationJob.target, unique=True, postgresql_where=_ACTIVE_OPT_JOB)
Index("idx_optimization_jobs_created", OptimizationJob.created_at.desc(), OptimizationJob.id.desc())
Index(
    "idx_optimization_jobs_target_created",
    OptimizationJob.target,
    OptimizationJob.created_at.desc(),
    OptimizationJob.id.desc(),
)
Index(
    "idx_optimization_jobs_decision_provider_id",
    OptimizationJob.decision_provider_id,
    postgresql_where=OptimizationJob.decision_provider_id.is_not(None),
)


class OptimizationPromotion(Base):
    __tablename__ = "optimization_promotions"
    __table_args__ = (
        CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')", name="ck_optimization_promotions_target"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    target: Mapped[str] = mapped_column(Text)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("optimization_jobs.id", ondelete="RESTRICT"))
    artifact_object_key: Mapped[str] = mapped_column(Text)
    artifact_sha256: Mapped[str] = mapped_column(CHAR(64))
    artifact_size_bytes: Mapped[int] = mapped_column(BigInteger)
    resolved_decision_model: Mapped[str | None] = mapped_column(Text)
    decision_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "llm_providers.id",
            name="fk_optimization_promotions_decision_provider_id",
            ondelete="SET NULL",
        )
    )
    program_kind: Mapped[str | None] = mapped_column(Text, server_default=text("'rlm'"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    _OPT_PROMOTION_LOCK_INDEX,
    OptimizationPromotion.target,
    unique=True,
    postgresql_where=_ACTIVE_OPT_PROMOTION,
)
Index(
    "idx_optimization_promotions_target_promoted",
    OptimizationPromotion.target,
    OptimizationPromotion.promoted_at.desc(),
    OptimizationPromotion.id.desc(),
)


class SessionNotFoundError(LookupError):
    pass


class TurnConflictError(RuntimeError):
    pass


class UploadConflictError(TurnConflictError):
    pass


class OptimizationConflictError(RuntimeError):
    pass


def upload_is_active(session: Session) -> bool:
    started = session.upload_started_at
    return started is not None and datetime.now(UTC) - started < _UPLOAD_LEASE


def create_db(settings: Settings) -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(
        settings.database_url,
        connect_args={"prepare_threshold": None},
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle_seconds,
        hide_parameters=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def create_session(
    db: SessionFactory, *, title: str = "New session", model_override: str | None = None
) -> Session:
    async with db.begin() as connection:
        await lock_model_providers(connection, (model_override,))
        session = Session(title=title, model_override=model_override)
        connection.add(session)
    return session


async def get_session_or_raise(db: SessionFactory, session_id: UUID) -> Session:
    async with db() as connection:
        session = await connection.get(Session, session_id)
        if session is None or session.deleting:
            raise SessionNotFoundError(f"Session {session_id} does not exist")
        return session


async def rebind_session_sandbox(
    db: SessionFactory,
    session_id: UUID,
    sandbox_id: str | None,
    *,
    expected: str | None = None,
    owner_run_id: UUID | None = None,
    allocation_name: str | None = None,
) -> tuple[str | None, bool]:
    """Compare-and-swap the current allocation without holding a provider connection."""
    async with db.begin() as connection:
        session = await connection.get(Session, session_id, with_for_update=SESSION_WRITE_LOCK)
        if session is None or session.deleting:
            raise SessionNotFoundError(f"Session {session_id} does not exist")
        if owner_run_id is not None:
            await _require_active_root(connection, session_id, owner_run_id)
        if session.daytona_sandbox_id != expected:
            return session.daytona_sandbox_id, False
        if allocation_name is not None:
            if session.pending_sandbox_name != allocation_name:
                return session.daytona_sandbox_id, False
            session.pending_sandbox_name = None
        session.daytona_sandbox_id = sandbox_id
        session.sandbox_needs_cleanup = False
        return sandbox_id, True


async def _require_active_root(connection: AsyncSession, session_id: UUID, run_id: UUID) -> None:
    owner = await connection.get(Run, run_id)
    if (
        owner is None
        or owner.session_id != session_id
        or owner.parent_run_id is not None
        or owner.status != "running"
    ):
        raise TurnConflictError("Workspace allocation no longer belongs to an active turn")


async def reserve_session_sandbox(
    db: SessionFactory, session_id: UUID, owner_run_id: UUID, *, expected: str | None
) -> tuple[str, bool]:
    """Commit the create identity before sending the provider request."""
    async with db.begin() as connection:
        session = await connection.get(Session, session_id, with_for_update=SESSION_WRITE_LOCK)
        if session is None or session.deleting:
            raise SessionNotFoundError(session_id)
        await _require_active_root(connection, session_id, owner_run_id)
        if session.daytona_sandbox_id != expected:
            raise TurnConflictError("Workspace allocation changed while reserving a sandbox")
        if session.pending_sandbox_name is not None:
            return session.pending_sandbox_name, True
        name = f"fleet-{owner_run_id.hex}"
        session.pending_sandbox_name = name
        return name, False


async def acquire_root_run(db: SessionFactory, session_id: UUID, prompt: str) -> Run:
    # One server-generated UUID identifies both the row and its run tree.
    root = select(func.uuidv7(type_=Uuid).label("id")).cte()
    statement = (
        insert(Run)
        .from_select(
            ["id", "session_id", "root_run_id", "depth", "kind", "prompt"],
            select(
                root.c.id,
                literal(session_id, Uuid),
                root.c.id,
                literal(0, SmallInteger),
                literal("root", Text),
                literal(prompt, Text),
            ),
        )
        .returning(Run)
    )
    try:
        async with db.begin() as connection:
            session = await connection.get(Session, session_id, with_for_update=SESSION_WRITE_LOCK)
            if session is None or session.deleting:
                raise SessionNotFoundError(session_id)
            if upload_is_active(session):
                raise UploadConflictError(f"Session {session_id} already has an upload in progress")
            if session.upload_key is not None:
                session.upload_key = session.upload_started_at = None
            if session.title == "New session":
                previous_root = await connection.scalar(
                    select(Run.id).where(Run.session_id == session_id, Run.kind == "root").limit(1)
                )
                if previous_root is None:
                    title = " ".join(prompt.split())
                    if title:
                        session.title = title if len(title) <= 80 else title[:79].rstrip() + "…"
            return (await connection.scalars(statement)).one()
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == _ROOT_LOCK_INDEX:
            raise TurnConflictError(f"Session {session_id} already has a running root") from exc
        raise


async def create_child_run(
    db: SessionFactory,
    parent_run_id: UUID,
    *,
    kind: Literal["subagent", "llm_query"],
    prompt: str,
) -> Run:
    if kind not in ("subagent", "llm_query"):
        raise ValueError("create_child_run requires a child kind")

    async with db.begin() as connection:
        root_id = await connection.scalar(select(Run.root_run_id).where(Run.id == parent_run_id))
        if root_id is None:
            raise LookupError(f"Parent run {parent_run_id} does not exist")

        # Lock root before parent. Shared row locks admit siblings but serialize with finish/reap.
        root = await connection.get(Run, root_id, with_for_update={"read": True})
        parent = (
            root
            if parent_run_id == root_id
            else await connection.get(Run, parent_run_id, with_for_update={"read": True})
        )
        if root is None or root.status != "running" or parent is None or parent.status != "running":
            raise TurnConflictError(f"Parent run {parent_run_id} is no longer active")

        child = Run(
            session_id=parent.session_id,
            parent_run_id=parent.id,
            root_run_id=root.id,
            depth=parent.depth + 1,
            kind=kind,
            prompt=prompt,
        )
        connection.add(child)
    return child


def transcript_value(value: Any) -> Any:
    """Represent NUL in transcript text visibly. Postgres text and JSONB reject U+0000."""
    if isinstance(value, str):
        return value.replace("\x00", "␀")
    if isinstance(value, list):
        return [transcript_value(item) for item in value]
    if isinstance(value, dict):
        # Keep field names intact. Invalid object keys must fail rather than collide.
        return {key: transcript_value(item) for key, item in value.items()}
    return value


async def finish_run(
    db: SessionFactory,
    run_id: UUID,
    *,
    status: TerminalStatus,
    answer: str | None = None,
    ui_parts: list[JsonObject] | None = None,
    error: str | None = None,
    history_json: JsonObject | None = None,
    files: list[StagedFile] | None = None,
    retire_sandbox_id: str | None = None,
) -> bool:
    """Commit the terminal result and any root history together. Return false on a late retry."""
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError("finish_run requires a terminal status")

    async with db.begin() as connection:
        await connection.scalar(
            select(Session.id)
            .join(Run, Run.session_id == Session.id)
            .where(Run.id == run_id, Run.parent_run_id.is_(None), Run.status == "running")
            .with_for_update(of=Session, key_share=True)
        )
        result = await connection.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "running")
            .values(
                status=status,
                answer=transcript_value(answer),
                ui_parts=transcript_value(ui_parts or []),
                error=transcript_value(error),
                finished_at=func.now(),
            )
            .returning(Run.session_id, Run.parent_run_id)
        )
        row = result.one_or_none()
        if row is None:
            return False
        if row.parent_run_id is None and history_json is not None:
            await connection.execute(
                update(Session)
                .where(Session.id == row.session_id)
                .values(history_json=transcript_value(history_json))
            )
        if row.parent_run_id is None and retire_sandbox_id is not None:
            await connection.execute(
                update(Session)
                .where(
                    Session.id == row.session_id, Session.daytona_sandbox_id == retire_sandbox_id
                )
                .values(sandbox_needs_cleanup=True)
            )
        if files:
            await _publish_files(connection, row.session_id, files)
    return True


async def reap_stale_runs(
    db: SessionFactory,
    timeout_seconds: int,
    *,
    session_id: UUID | None = None,
) -> int:
    """Fail expired runs and running descendants of expired roots in one transaction."""
    cutoff = func.now() - timedelta(seconds=timeout_seconds)
    expired = (
        update(Run)
        .where(Run.status == "running")
        .values(status="failed", error="Run expired", finished_at=func.now())
    )
    if session_id is not None:
        expired = expired.where(Run.session_id == session_id)
    async with db.begin() as connection:
        root_is_stale = (
            Run.parent_run_id.is_(None),
            Run.status == "running",
            Run.created_at < cutoff,
        )
        candidates = select(Run.session_id).where(*root_is_stale)
        if session_id is not None:
            candidates = candidates.where(Run.session_id == session_id)
        sessions = (
            await connection.scalars(
                select(Session)
                .where(Session.id.in_(candidates))
                .order_by(Session.id)
                .with_for_update(key_share=True)
            )
        ).all()
        roots = (
            await connection.execute(
                expired.where(
                    *root_is_stale, Run.session_id.in_([session.id for session in sessions])
                ).returning(Run.id, Run.session_id)
            )
        ).all()
        expired_sessions = {root.session_id for root in roots}
        for session in sessions:
            if session.id in expired_sessions and session.daytona_sandbox_id is not None:
                session.sandbox_needs_cleanup = True
        child_condition = Run.created_at < cutoff
        if roots:
            child_condition = child_condition | Run.root_run_id.in_([root.id for root in roots])
        children = await connection.execute(
            expired.where(
                Run.parent_run_id.is_not(None),
                child_condition,
            ).execution_options(synchronize_session=False)
        )
        reaped_children = max(getattr(children, "rowcount", 0) or 0, 0)
        return len(roots) + reaped_children


async def get_session_ui_messages(db: SessionFactory, session_id: UUID) -> list[JsonObject]:
    messages, _ = await get_session_ui_message_page(db, session_id)
    return messages


async def get_session_ui_message_page(
    db: SessionFactory, session_id: UUID, *, limit: int | None = None, before: UUID | None = None
) -> tuple[list[JsonObject], UUID | None]:
    query = select(Run.id, Run.prompt, Run.ui_parts).where(
        Run.session_id == session_id,
        Run.parent_run_id.is_(None),
        Run.status != "running",
    )
    if before is not None:
        query = query.where(Run.id < before)
    if limit is None:
        query = query.order_by(Run.id)
    else:
        query = query.order_by(Run.id.desc()).limit(limit + 1)
    async with db() as connection:
        runs = (await connection.execute(query)).all()

    next_before = None
    if limit is not None:
        if len(runs) > limit:
            next_before = runs[limit - 1].id
        runs = list(reversed(runs[:limit]))

    messages = []
    for run_id, prompt, ui_parts in runs:
        messages.append(
            {
                "id": f"{run_id}:user",
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
            }
        )
        if ui_parts:
            messages.append({"id": str(run_id), "role": "assistant", "parts": ui_parts})
    return messages, next_before


async def get_turn_for_refinement(
    db: SessionFactory, session_id: UUID, run_id: UUID
) -> tuple[Run, Memory | None] | None:
    async with db() as connection:
        row = (
            await connection.execute(
                select(Run, Memory)
                .join(Session, Session.id == Run.session_id)
                .outerjoin(Memory, Memory.source_run_id == Run.id)
                .where(
                    Run.id == run_id,
                    Run.session_id == session_id,
                    Run.parent_run_id.is_(None),
                    Session.deleting.is_(False),
                )
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None


async def save_turn_lesson(
    db: SessionFactory,
    session_id: UUID,
    run_id: UUID,
    *,
    content: str,
    skill_name: str | None,
) -> Memory:
    async with db.begin() as connection:
        # Recheck the source after the model call and serialize concurrent saves for this session.
        source = await connection.scalar(
            select(Session.id)
            .join(Run, Run.session_id == Session.id)
            .where(
                Session.id == session_id,
                Session.deleting.is_(False),
                Run.id == run_id,
                Run.parent_run_id.is_(None),
                Run.status == "completed",
            )
            .with_for_update(of=Session, **SESSION_WRITE_LOCK)
        )
        if source is None:
            raise SessionNotFoundError(session_id)
        existing = await connection.scalar(select(Memory).where(Memory.source_run_id == run_id))
        if existing is not None:
            return existing
        memory = Memory(
            session_id=session_id, source_run_id=run_id, content=content, skill_name=skill_name
        )
        connection.add(memory)
    return memory


async def get_session_prior_turns(
    db: SessionFactory, session_id: UUID, *, max_turns: int, max_chars: int
) -> list[dict[str, str]]:
    """Read bounded prompt/answer pairs from previous successful root turns."""
    if max_turns <= 0 or max_chars <= 0:
        return []
    async with db() as connection:
        rows = (
            await connection.execute(
                select(
                    func.left(Run.prompt, max_chars),
                    func.left(Run.answer, max_chars),
                    func.jsonb_path_query_first(
                        Run.ui_parts, cast('$[*] ? (@.type == "data-turn-details").data', JSONPATH)
                    ),
                )
                .where(
                    Run.session_id == session_id,
                    Run.parent_run_id.is_(None),
                    Run.status == "completed",
                    Run.answer.is_not(None),
                )
                .order_by(Run.id.desc())
                .limit(max_turns)
            )
        ).all()

    turns = []
    remaining = max_chars
    for prompt, answer, details in rows:
        user = prompt[:remaining]
        assistant = answer[: remaining - len(user)]
        turn = {"user": user, "assistant": assistant}
        if details and details.get("runtime") == "dspy.RLM":
            turn["runtime"] = (
                f"dspy.RLM, {details['root_actions']} root actions, "
                f"{details['child_rlms']} child RLMs started, "
                f"{details['sub_lm_queries']} sub-LM queries launched. "
                f"Completion: {details['completion']}."
            )
        turns.append(turn)
        remaining -= len(user) + len(assistant)
        if remaining == 0:
            break
    turns.reverse()
    return turns


async def upsert_file_record(
    db: SessionFactory,
    session_id: UUID,
    *,
    path: str,
    sha256: str,
    size_bytes: int,
    kind: Literal["input", "artifact"],
    object_key: str,
    expected_upload: str | None = None,
) -> File:
    async with db.begin() as connection:
        if expected_upload is not None:
            session = await connection.get(Session, session_id, with_for_update=SESSION_WRITE_LOCK)
            if session is None or session.deleting or session.upload_key != expected_upload:
                raise SessionNotFoundError(session_id)
            session.upload_key = session.upload_started_at = None
        return (
            await _publish_files(
                connection,
                session_id,
                [
                    dict(
                        path=path,
                        sha256=sha256,
                        size_bytes=size_bytes,
                        kind=kind,
                        object_key=object_key,
                    )
                ],
            )
        )[0]


def _file_upsert(session_id: UUID, *, path: str, **values: Any):
    return (
        insert(File)
        .values(session_id=session_id, path=path, **values)
        .on_conflict_do_update(constraint="uq_files_session_path", set_=values)
        .returning(File)
    )


def replaced_file_paths(existing: Iterable[str], new: Iterable[str]) -> list[str]:
    replacements = {PurePosixPath(name) for name in new}
    ancestors = {parent for path in replacements for parent in path.parents}
    if replacements & ancestors:
        raise ValueError("Artifact paths cannot be both files and directories")

    shadowed = []
    for name in existing:
        path = PurePosixPath(name)
        if path in ancestors or any(parent in replacements for parent in path.parents):
            shadowed.append(name)
    return shadowed


async def _publish_files(
    connection: AsyncSession, session_id: UUID, files: list[StagedFile]
) -> list[File]:
    existing = dict(
        (
            await connection.execute(
                select(File.path, File.object_key).where(File.session_id == session_id)
            )
        ).all()
    )
    incoming = {file["path"]: file["object_key"] for file in files}
    shadowed = replaced_file_paths(existing, incoming)
    obsolete = {existing[path] for path in shadowed} | {
        existing[path]
        for path, key in incoming.items()
        if path in existing and existing[path] != key
    }
    if obsolete:
        await connection.execute(
            insert(ObjectCleanup)
            .values([{"session_id": session_id, "object_key": key} for key in obsolete])
            .on_conflict_do_nothing(index_elements=[ObjectCleanup.object_key])
        )
    if shadowed:
        await connection.execute(
            delete(File).where(File.session_id == session_id, File.path.in_(shadowed))
        )
    published = [
        (await connection.scalars(_file_upsert(session_id, **file))).one() for file in files
    ]
    await connection.execute(
        delete(ObjectCleanup).where(
            ObjectCleanup.session_id == session_id, ObjectCleanup.object_key.in_(incoming.values())
        )
    )
    return published


async def register_object_write(
    db: SessionFactory,
    session_id: UUID,
    key: str,
    upload_id: str,
    *,
    run_id: UUID | None = None,
) -> None:
    async with db.begin() as connection:
        session = await connection.get(Session, session_id, with_for_update=SESSION_WRITE_LOCK)
        if session is None or session.deleting or (run_id is None and session.upload_key != key):
            raise SessionNotFoundError(session_id)
        if run_id is not None:
            await _require_active_root(connection, session_id, run_id)
        connection.add(
            ObjectCleanup(session_id=session_id, object_key=key, upload_id=upload_id, run_id=run_id)
        )


async def list_discarded_objects(db: SessionFactory, session_id: UUID) -> list[ObjectCleanup]:
    active_root = select(Run.id).where(Run.id == ObjectCleanup.run_id, Run.status == "running")
    referenced = select(File.id).where(
        File.session_id == ObjectCleanup.session_id, File.object_key == ObjectCleanup.object_key
    )
    async with db() as connection:
        rows = await connection.scalars(
            select(ObjectCleanup)
            .join(Session, Session.id == ObjectCleanup.session_id)
            .where(
                ObjectCleanup.session_id == session_id,
                ~referenced.exists(),
                (
                    ObjectCleanup.run_id.is_(None)
                    & Session.upload_key.is_distinct_from(ObjectCleanup.object_key)
                )
                | (ObjectCleanup.run_id.is_not(None) & ~active_root.exists()),
            )
        )
        return list(rows)


async def forget_discarded_objects(db: SessionFactory, keys: list[str]) -> None:
    async with db.begin() as connection:
        await connection.execute(delete(ObjectCleanup).where(ObjectCleanup.object_key.in_(keys)))


async def release_upload(db: SessionFactory, session_id: UUID, key: str) -> None:
    async with db.begin() as connection:
        await connection.execute(
            update(Session)
            .where(Session.id == session_id, Session.upload_key == key)
            .values(upload_key=None, upload_started_at=None)
        )


async def list_session_files(db: SessionFactory, session_id: UUID) -> list[File]:
    async with db() as connection:
        result = await connection.scalars(
            select(File).where(File.session_id == session_id).order_by(File.path)
        )
        return list(result)


async def search_memories(
    db: SessionFactory,
    session_id: UUID | None,
    query: str,
    *,
    limit: int = 10,
    include_unapproved: bool = False,
) -> list[Memory]:
    scope = Memory.session_id.is_(None)
    if session_id is not None:
        scope = scope | (Memory.session_id == session_id)
    filters = [scope, Memory.source_run_id.is_(None)]
    if not include_unapproved:
        filters.append(Memory.approved.is_(True))
    async with db() as connection:
        return await _match_memories(connection, query, filters, limit)


async def _match_memories(
    connection: AsyncSession, query: str, filters: list[ColumnElement[bool]], limit: int
) -> list[Memory]:
    strict_q = func.websearch_to_tsquery("english", query)
    relaxed_q = cast(
        func.replace(cast(func.plainto_tsquery("english", query), Text), " & ", " | "),
        TSQUERY,
    )
    query_lexemes = func.tsvector_to_array(func.to_tsvector("english", query))
    memory_lexeme = func.unnest(func.tsvector_to_array(Memory.search_tsv)).column_valued("lexeme")
    overlap_count = (
        select(func.count()).where(memory_lexeme == func.any_(query_lexemes)).scalar_subquery()
    )
    min_overlap = case((func.cardinality(query_lexemes) <= 2, 1), else_=2)
    matches = list(
        await connection.scalars(
            select(Memory)
            .where(*filters, Memory.search_tsv.bool_op("@@")(strict_q))
            .order_by(func.ts_rank(Memory.search_tsv, strict_q).desc(), Memory.id.desc())
            .limit(limit)
        )
    )
    if len(matches) < limit:
        relaxed_stmt = (
            select(Memory)
            .where(
                *filters,
                Memory.search_tsv.bool_op("@@")(relaxed_q),
                overlap_count >= min_overlap,
            )
            .order_by(
                overlap_count.desc(),
                func.ts_rank(Memory.search_tsv, relaxed_q).desc(),
                Memory.id.desc(),
            )
            .limit(limit - len(matches))
        )
        if matches:
            relaxed_stmt = relaxed_stmt.where(Memory.id.not_in([memory.id for memory in matches]))
        matches.extend(await connection.scalars(relaxed_stmt))
    return matches


async def recall_procedures(
    db: SessionFactory, session_id: UUID, query: str, selected_skills: list[str]
) -> list[Memory]:
    """Prefer lessons for active skills, then match general lessons against the task."""
    limit = 2
    filters = [
        Memory.session_id == session_id,
        Memory.approved.is_(True),
        Memory.source_run_id.is_not(None),
    ]
    async with db() as connection:
        lessons = []
        if selected_skills:
            lessons = list(
                await connection.scalars(
                    select(Memory)
                    .where(*filters, Memory.skill_name.in_(selected_skills))
                    .order_by(Memory.id.desc())
                    .limit(limit)
                )
            )
        if len(lessons) < limit:
            lessons.extend(
                await _match_memories(
                    connection, query, [*filters, Memory.skill_name.is_(None)], limit - len(lessons)
                )
            )
        return lessons


async def list_approved_session_memories(
    db: SessionFactory, session_id: UUID, *, limit: int = 10
) -> list[Memory]:
    """Return the most recent approved factual memories belonging to this session."""
    async with db() as connection:
        result = await connection.scalars(
            select(Memory)
            .where(
                Memory.session_id == session_id,
                Memory.approved.is_(True),
                Memory.source_run_id.is_(None),
            )
            .order_by(Memory.id.desc())
            .limit(limit)
        )
        return list(result)


async def save_or_update_memory(
    db: SessionFactory,
    *,
    session_id: UUID | None,
    content: str,
    approved: bool = False,
    memory_id: UUID | None = None,
) -> Memory:
    async with db.begin() as connection:
        if memory_id is None:
            memory = Memory(session_id=session_id, content=content, approved=approved)
            connection.add(memory)
        else:
            memory = await connection.scalar(
                update(Memory)
                .where(
                    Memory.id == memory_id,
                    Memory.session_id == session_id,
                    Memory.source_run_id.is_(None),
                )
                .values(content=content, approved=approved)
                .returning(Memory)
            )
            if memory is None:
                raise LookupError(f"Memory {memory_id} does not exist in this scope")
    return memory


async def get_workspace_settings_overrides(db: SessionFactory) -> dict[str, Any]:
    async with db() as connection:
        row = await connection.get(WorkspaceSettings, 1)
        if row is None or not row.overrides_json:
            return {}
        return dict(row.overrides_json)


_WORKSPACE_MODEL_ROLES = {
    "llm_model": "Primary LLM",
    "sub_llm_model": "Sub-LLM",
    "decision_model": "Decision Model",
}


def model_provider_id(value: object) -> UUID | None:
    if not isinstance(value, str) or not value.startswith("provider/"):
        return None
    try:
        _, provider_id, name = value.split("/", 2)
        if name.strip():
            return UUID(provider_id)
    except ValueError:
        pass
    raise OptimizationConflictError("Select a valid provider and model")


async def lock_model_providers(connection: AsyncSession, models: Iterable[object]) -> None:
    provider_ids = {
        provider_id for model in models if (provider_id := model_provider_id(model)) is not None
    }
    for provider_id in sorted(provider_ids):
        provider = await connection.get(LLMProvider, provider_id, with_for_update=True)
        if provider is None:
            raise OptimizationConflictError("The selected provider no longer exists")


async def update_workspace_settings_overrides(
    db: SessionFactory, changes: dict[str, Any]
) -> dict[str, Any]:
    async with db.begin() as connection:
        await lock_model_providers(
            connection, (value for key, value in changes.items() if key in _WORKSPACE_MODEL_ROLES)
        )
        await connection.execute(
            insert(WorkspaceSettings)
            .values(id=1, overrides_json={})
            .on_conflict_do_nothing(index_elements=[WorkspaceSettings.id])
        )
        row = await connection.get(WorkspaceSettings, 1, with_for_update=True)
        if row is None:
            raise RuntimeError("Workspace settings singleton row could not be initialized")
        merged = dict(row.overrides_json or {})
        for key, value in changes.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        row.overrides_json = merged
    return dict(merged)


async def reset_workspace_settings_overrides(db: SessionFactory) -> dict[str, Any]:
    statement = (
        insert(WorkspaceSettings)
        .values(id=1, overrides_json={})
        .on_conflict_do_update(
            index_elements=[WorkspaceSettings.id],
            set_={"overrides_json": {}, "updated_at": func.now()},
        )
    )
    async with db.begin() as connection:
        await connection.execute(statement)
    return {}


def _config_references_provider(config: Any, provider_id_str: str) -> bool:
    if isinstance(config, dict):
        for key, value in config.items():
            if key == "provider_id" and value == provider_id_str:
                return True
            if key.endswith("_provider_id") and value == provider_id_str:
                return True
            if key == "provider_ids" and isinstance(value, list) and provider_id_str in value:
                return True
            if isinstance(value, (dict, list)) and _config_references_provider(
                value, provider_id_str
            ):
                return True
    elif isinstance(config, list):
        return any(_config_references_provider(item, provider_id_str) for item in config)
    return False


async def list_llm_providers(db: SessionFactory) -> list[LLMProvider]:
    async with db() as connection:
        rows = await connection.scalars(
            select(LLMProvider).order_by(LLMProvider.name.asc(), LLMProvider.id.asc())
        )
        return list(rows)


async def get_llm_provider(db: SessionFactory, provider_id: UUID) -> LLMProvider | None:
    async with db() as connection:
        return await connection.get(LLMProvider, provider_id)


async def get_llm_provider_by_name(db: SessionFactory, name: str) -> LLMProvider | None:
    async with db() as connection:
        return await connection.scalar(select(LLMProvider).where(LLMProvider.name == name))


async def create_llm_provider(
    db: SessionFactory,
    *,
    name: str,
    dialect: LLMProviderDialect,
    api_mode: LLMProviderApiMode = "auto",
    base_url: str | None = None,
    api_key_ciphertext: str | None = None,
    api_key_hint: str | None = None,
    default_model: str | None = None,
    cached_models_json: list[JsonObject] | None = None,
    last_tested_at: datetime | None = None,
) -> LLMProvider:
    try:
        async with db.begin() as connection:
            provider = LLMProvider(
                name=name,
                dialect=dialect,
                api_mode=api_mode,
                base_url=base_url,
                api_key_ciphertext=api_key_ciphertext,
                api_key_hint=api_key_hint,
                default_model=default_model,
                cached_models_json=cached_models_json,
                last_tested_at=last_tested_at,
            )
            connection.add(provider)
        return provider
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == _LLM_PROVIDER_NAME_CONSTRAINT
        ):
            raise OptimizationConflictError(
                f"An LLM provider named '{name}' already exists"
            ) from exc
        raise


async def update_llm_provider(
    db: SessionFactory,
    provider_id: UUID,
    *,
    expected_updated_at: datetime | None = None,
    name: str = _UNSET,
    dialect: LLMProviderDialect = _UNSET,
    api_mode: LLMProviderApiMode = _UNSET,
    base_url: str | None = _UNSET,
    api_key_ciphertext: str | None = _UNSET,
    api_key_hint: str | None = _UNSET,
    default_model: str | None = _UNSET,
    cached_models_json: list[JsonObject] | None = _UNSET,
    last_tested_at: datetime | None = _UNSET,
) -> LLMProvider | None:
    values: dict[str, Any] = {}
    if name is not _UNSET:
        values["name"] = name
    if dialect is not _UNSET:
        values["dialect"] = dialect
    if api_mode is not _UNSET:
        values["api_mode"] = api_mode
    if base_url is not _UNSET:
        values["base_url"] = base_url
    if api_key_ciphertext is not _UNSET:
        values["api_key_ciphertext"] = api_key_ciphertext
    if api_key_hint is not _UNSET:
        values["api_key_hint"] = api_key_hint
    if default_model is not _UNSET:
        values["default_model"] = default_model
    if cached_models_json is not _UNSET:
        values["cached_models_json"] = cached_models_json
    if last_tested_at is not _UNSET:
        values["last_tested_at"] = last_tested_at
    if not values:
        return await get_llm_provider(db, provider_id)
    values["updated_at"] = func.now()
    try:
        async with db.begin() as connection:
            stmt = update(LLMProvider).where(LLMProvider.id == provider_id)
            if expected_updated_at is not None:
                stmt = stmt.where(LLMProvider.updated_at == expected_updated_at)
            row = await connection.scalar(stmt.values(**values).returning(LLMProvider))
            if row is None and await connection.get(LLMProvider, provider_id) is not None:
                raise OptimizationConflictError("Provider changed during the request; retry")
            return row
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == _LLM_PROVIDER_NAME_CONSTRAINT
        ):
            raise OptimizationConflictError(
                f"An LLM provider named '{name}' already exists"
            ) from exc
        raise


def _guard_workspace_provider_removal(overrides: dict[str, Any], provider_id: UUID) -> None:
    roles = []
    for field, role in _WORKSPACE_MODEL_ROLES.items():
        with suppress(OptimizationConflictError):
            if model_provider_id(overrides.get(field)) == provider_id:
                roles.append(role)
    if roles:
        raise OptimizationConflictError(
            f"Provider is assigned to workspace defaults: {', '.join(roles)}. "
            "Choose a replacement in Models before deleting it."
        )


async def delete_llm_provider(db: SessionFactory, provider_id: UUID) -> LLMProvider | None:
    async with db.begin() as connection:
        provider = await connection.get(LLMProvider, provider_id, with_for_update=True)
        if provider is None:
            return None
        workspace = await connection.get(WorkspaceSettings, 1)
        overrides = workspace.overrides_json if workspace else {}
        _guard_workspace_provider_removal(overrides or {}, provider_id)
        active_promo = await connection.scalar(
            select(OptimizationPromotion.id)
            .where(
                OptimizationPromotion.decision_provider_id == provider_id,
                OptimizationPromotion.active.is_(True),
            )
            .limit(1)
        )
        if active_promo is not None:
            raise OptimizationConflictError(
                "LLM provider is currently referenced by an active optimization promotion"
            )
        # Match UUID spellings already accepted by the model resolver.
        session_provider = func.split_part(Session.model_override, "/", 2)
        normalized_provider = func.lower(
            func.regexp_replace(session_provider, "urn:|uuid:|[-{}]", "", "g")
        )
        session_ref = await connection.scalar(
            select(Session.id)
            .where(
                Session.model_override.like("provider/%/_%"), normalized_provider == provider_id.hex
            )
            .limit(1)
        )
        if session_ref is not None:
            raise OptimizationConflictError(
                "Provider is assigned to a session model override. "
                "Choose a replacement in the session before deleting it."
            )
        active_jobs = (
            await connection.scalars(
                select(OptimizationJob).where(OptimizationJob.status.in_(("queued", "running")))
            )
        ).all()
        provider_id_str = str(provider_id)
        for job in active_jobs:
            if job.decision_provider_id == provider_id or _config_references_provider(
                job.config_json, provider_id_str
            ):
                raise OptimizationConflictError(
                    "LLM provider is currently in use by an active optimization job"
                )
        await connection.delete(provider)
        return provider


async def create_optimization_dataset(
    db: SessionFactory,
    *,
    name: str,
    target: OptimizationTarget,
    object_key: str,
    sha256: str,
    size_bytes: int,
    row_count: int,
    summary_json: JsonObject,
    storage_backend: OptimizationStorageBackend = "neon",
    storage_volume: str | None = None,
    dataset_id: UUID | None = None,
) -> OptimizationDataset:
    async with db.begin() as connection:
        record = OptimizationDataset(
            **({"id": dataset_id} if dataset_id is not None else {}),
            name=name,
            target=target,
            storage_backend=storage_backend,
            storage_volume=storage_volume,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
            row_count=row_count,
            summary_json=summary_json,
        )
        connection.add(record)
    return record


async def list_optimization_datasets(db: SessionFactory) -> list[OptimizationDataset]:
    async with db() as connection:
        rows = await connection.scalars(
            select(OptimizationDataset).order_by(
                OptimizationDataset.created_at.desc(), OptimizationDataset.id.desc()
            )
        )
        return list(rows)


async def get_optimization_dataset(
    db: SessionFactory, dataset_id: UUID
) -> OptimizationDataset | None:
    async with db() as connection:
        return await connection.get(OptimizationDataset, dataset_id)


async def get_optimization_dataset_by_name(
    db: SessionFactory, name: str
) -> OptimizationDataset | None:
    async with db() as connection:
        return await connection.scalar(
            select(OptimizationDataset).where(OptimizationDataset.name == name)
        )


async def begin_optimization_dataset_deletion(
    db: SessionFactory, dataset_id: UUID
) -> OptimizationDataset | None:
    async with db.begin() as connection:
        record = await connection.get(OptimizationDataset, dataset_id, with_for_update=True)
        if record is None:
            return None
        active_ref = await connection.scalar(
            select(OptimizationJob.id).where(
                OptimizationJob.dataset_id == dataset_id,
                OptimizationJob.status.in_(("queued", "running")),
            )
        )
        if active_ref is not None:
            raise OptimizationConflictError("Dataset is currently in use by an active job")
        record.summary_json = {**record.summary_json, "deletion_pending": True}
        return record


async def finish_optimization_dataset_deletion(db: SessionFactory, dataset_id: UUID) -> None:
    async with db.begin() as connection:
        await connection.execute(
            delete(OptimizationDataset).where(OptimizationDataset.id == dataset_id)
        )


async def create_optimization_job(
    db: SessionFactory,
    *,
    target: OptimizationTarget,
    program_kind: str | None,
    dataset_source: OptimizationDatasetSource,
    dataset_name: str | None,
    dataset_id: UUID | None,
    config_json: JsonObject,
    mlflow_experiment: str | None,
    decision_provider_id: UUID | None = None,
) -> OptimizationJob:
    try:
        async with db.begin() as connection:
            if dataset_id is not None:
                dataset = await connection.get(
                    OptimizationDataset,
                    dataset_id,
                    with_for_update={"key_share": True},
                )
                if dataset is None:
                    raise LookupError(f"Optimization dataset {dataset_id} does not exist")
                if dataset.summary_json.get("deletion_pending"):
                    raise OptimizationConflictError("Dataset deletion is pending")
            if decision_provider_id is not None:
                provider = await connection.get(
                    LLMProvider,
                    decision_provider_id,
                    with_for_update={"key_share": True},
                )
                if provider is None:
                    raise LookupError(f"LLM provider {decision_provider_id} does not exist")
            job = OptimizationJob(
                target=target,
                program_kind=program_kind,
                status="queued",
                stage="queued",
                dataset_source=dataset_source,
                dataset_name=dataset_name,
                dataset_id=dataset_id,
                decision_provider_id=decision_provider_id,
                config_json=config_json,
                progress_json={},
                mlflow_experiment=mlflow_experiment,
            )
            connection.add(job)
        return job
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == _OPT_JOB_LOCK_INDEX:
            raise OptimizationConflictError(
                f"Target '{target}' already has an active optimization job"
            ) from exc
        raise


async def touch_optimization_job_heartbeat(db: SessionFactory, job_id: UUID) -> bool:
    """Refresh heartbeat_at for an active optimization job."""
    async with db.begin() as connection:
        result = await connection.execute(
            update(OptimizationJob)
            .where(
                OptimizationJob.id == job_id,
                OptimizationJob.status.in_(("queued", "running")),
            )
            .values(heartbeat_at=func.now())
        )
        return max(getattr(result, "rowcount", 0) or 0, 0) > 0


async def update_optimization_job_progress(
    db: SessionFactory,
    job_id: UUID,
    *,
    status: Literal["queued", "running"] | None = None,
    stage: str | None = None,
    progress_json: JsonObject | None = None,
    log_tail: str | None = None,
    started_at: datetime | None = None,
) -> OptimizationJob | None:
    values: dict[str, Any] = {"heartbeat_at": func.now()}
    if status is not None:
        values["status"] = status
    if stage is not None:
        values["stage"] = stage
    if progress_json is not None:
        values["progress_json"] = progress_json
    if log_tail is not None:
        values["log_tail"] = log_tail
    if started_at is not None:
        values["started_at"] = started_at
    async with db.begin() as connection:
        return await connection.scalar(
            update(OptimizationJob)
            .where(
                OptimizationJob.id == job_id,
                OptimizationJob.status.in_(("queued", "running")),
            )
            .values(**values)
            .returning(OptimizationJob)
        )


async def finish_optimization_job(
    db: SessionFactory,
    job_id: UUID,
    *,
    status: TerminalStatus,
    stage: str,
    saved: bool | None = None,
    baseline_score: float | None = None,
    optimized_score: float | None = None,
    train_size: int | None = None,
    val_size: int | None = None,
    holdout_size: int | None = None,
    progress_json: JsonObject | None = None,
    report_json: JsonObject | None = None,
    log_tail: str | None = None,
    artifact_object_key: str | None = None,
    artifact_sha256: str | None = None,
    artifact_size_bytes: int | None = None,
    resolved_decision_model: str | None = None,
    decision_provider_id: UUID | None = None,
    mlflow_run_id: str | None = None,
    mlflow_experiment: str | None = None,
    error: str | None = None,
) -> OptimizationJob | None:
    values: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "heartbeat_at": func.now(),
        "finished_at": func.now(),
    }
    if saved is not None:
        values["saved"] = saved
    if baseline_score is not None:
        values["baseline_score"] = baseline_score
    if optimized_score is not None:
        values["optimized_score"] = optimized_score
    if train_size is not None:
        values["train_size"] = train_size
    if val_size is not None:
        values["val_size"] = val_size
    if holdout_size is not None:
        values["holdout_size"] = holdout_size
    if progress_json is not None:
        values["progress_json"] = progress_json
    if report_json is not None:
        values["report_json"] = report_json
    if log_tail is not None:
        values["log_tail"] = log_tail
    if artifact_object_key is not None:
        values["artifact_object_key"] = artifact_object_key
    if artifact_sha256 is not None:
        values["artifact_sha256"] = artifact_sha256
    if artifact_size_bytes is not None:
        values["artifact_size_bytes"] = artifact_size_bytes
    if resolved_decision_model is not None:
        values["resolved_decision_model"] = resolved_decision_model
    if decision_provider_id is not None:
        values["decision_provider_id"] = decision_provider_id
    if mlflow_run_id is not None:
        values["mlflow_run_id"] = mlflow_run_id
    if mlflow_experiment is not None:
        values["mlflow_experiment"] = mlflow_experiment
    if error is not None:
        values["error"] = error
    async with db.begin() as connection:
        return await connection.scalar(
            update(OptimizationJob)
            .where(
                OptimizationJob.id == job_id,
                OptimizationJob.status.in_(("queued", "running")),
            )
            .values(**values)
            .returning(OptimizationJob)
        )


async def request_optimization_job_cancel(
    db: SessionFactory, job_id: UUID
) -> OptimizationJob | None:
    async with db.begin() as connection:
        job = await connection.get(OptimizationJob, job_id, with_for_update=True)
        if job is None:
            return None
        if job.status in ("queued", "running"):
            job.cancel_requested = True
            if job.status == "queued":
                job.stage = "cancelling"
        return job


async def is_optimization_job_cancel_requested(db: SessionFactory, job_id: UUID) -> bool:
    async with db() as connection:
        flag = await connection.scalar(
            select(OptimizationJob.cancel_requested).where(OptimizationJob.id == job_id)
        )
        return bool(flag)


async def reap_stale_optimization_jobs(db: SessionFactory, timeout_seconds: int) -> int:
    cutoff = func.now() - timedelta(seconds=timeout_seconds)
    last_activity = func.coalesce(
        OptimizationJob.heartbeat_at,
        OptimizationJob.started_at,
        OptimizationJob.created_at,
    )
    async with db.begin() as connection:
        if connection is None:
            return 0
        result = await connection.execute(
            update(OptimizationJob)
            .where(
                OptimizationJob.status.in_(("queued", "running")),
                last_activity < cutoff,
            )
            .values(
                status="failed",
                stage="failed",
                error="Optimization job timed out or worker restarted",
                finished_at=func.now(),
            )
        )
        return max(getattr(result, "rowcount", 0) or 0, 0)


async def list_optimization_jobs(
    db: SessionFactory,
    *,
    target: OptimizationTarget | None = None,
    status: OptimizationJobStatus | None = None,
    limit: int = 50,
) -> list[OptimizationJob]:
    async with db() as connection:
        query = select(OptimizationJob)
        if target is not None:
            query = query.where(OptimizationJob.target == target)
        if status is not None:
            query = query.where(OptimizationJob.status == status)
        rows = await connection.scalars(
            query.order_by(OptimizationJob.created_at.desc(), OptimizationJob.id.desc()).limit(
                limit
            )
        )
        return list(rows)


async def get_optimization_job(db: SessionFactory, job_id: UUID) -> OptimizationJob | None:
    async with db() as connection:
        return await connection.get(OptimizationJob, job_id)


async def promote_optimization_artifact(
    db: SessionFactory,
    *,
    target: OptimizationTarget,
    job_id: UUID,
    artifact_object_key: str,
    artifact_sha256: str,
    artifact_size_bytes: int,
    resolved_decision_model: str | None,
    decision_provider_id: UUID | None = None,
    program_kind: str | None = None,
) -> OptimizationPromotion:
    try:
        async with db.begin() as connection:
            if decision_provider_id is not None:
                provider = await connection.get(
                    LLMProvider,
                    decision_provider_id,
                    with_for_update={"key_share": True},
                )
                if provider is None:
                    raise LookupError(f"LLM provider {decision_provider_id} does not exist")
            active_rows = (
                await connection.scalars(
                    select(OptimizationPromotion)
                    .where(
                        OptimizationPromotion.target == target,
                        OptimizationPromotion.active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()
            for current in active_rows:
                current.active = False
                current.rolled_back_at = func.now()
            await connection.flush()
            effective_program_kind = (
                program_kind if program_kind is not None else ("rlm" if target == "rlm" else None)
            )
            promotion = OptimizationPromotion(
                target=target,
                job_id=job_id,
                artifact_object_key=artifact_object_key,
                artifact_sha256=artifact_sha256,
                artifact_size_bytes=artifact_size_bytes,
                resolved_decision_model=resolved_decision_model,
                decision_provider_id=decision_provider_id,
                program_kind=effective_program_kind,
                active=True,
            )
            connection.add(promotion)
            return promotion
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == _OPT_PROMOTION_LOCK_INDEX
        ):
            raise OptimizationConflictError(
                f"Target '{target}' has a concurrent promotion in progress"
            ) from exc
        raise


async def rollback_optimization_target(
    db: SessionFactory,
    target: OptimizationTarget,
    *,
    mode: Literal["previous", "env"] = "previous",
) -> OptimizationPromotion | None:
    try:
        async with db.begin() as connection:
            current = await connection.scalar(
                select(OptimizationPromotion)
                .where(
                    OptimizationPromotion.target == target,
                    OptimizationPromotion.active.is_(True),
                )
                .with_for_update()
            )
            if current is None:
                return None
            current.active = False
            current.rolled_back_at = func.now()
            await connection.flush()
            if mode != "previous":
                return None
            previous = await connection.scalar(
                select(OptimizationPromotion)
                .where(
                    OptimizationPromotion.target == target,
                    OptimizationPromotion.active.is_(False),
                    tuple_(OptimizationPromotion.promoted_at, OptimizationPromotion.id)
                    < tuple_(current.promoted_at, current.id),
                )
                .order_by(OptimizationPromotion.promoted_at.desc(), OptimizationPromotion.id.desc())
                .limit(1)
                .with_for_update()
            )
            if previous is None:
                return None
            previous.active = True
            previous.rolled_back_at = None
            return previous
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == _OPT_PROMOTION_LOCK_INDEX
        ):
            raise OptimizationConflictError(
                f"Target '{target}' has a concurrent promotion or rollback in progress"
            ) from exc
        raise


async def list_active_optimization_promotions(
    db: SessionFactory,
) -> dict[str, OptimizationPromotion]:
    async with db() as connection:
        rows = await connection.scalars(
            select(OptimizationPromotion).where(OptimizationPromotion.active.is_(True))
        )
        return {row.target: row for row in rows}


async def list_target_promotion_history(
    db: SessionFactory, target: OptimizationTarget, limit: int = 10
) -> list[OptimizationPromotion]:
    async with db() as connection:
        rows = await connection.scalars(
            select(OptimizationPromotion)
            .where(OptimizationPromotion.target == target)
            .order_by(OptimizationPromotion.promoted_at.desc(), OptimizationPromotion.id.desc())
            .limit(limit)
        )
        return list(rows)
