"""Postgres models and short, session-scoped repository operations."""

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    case,
    cast,
    func,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, TSQUERY, TSVECTOR, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fleet_rlm.config import Settings

_ROOT_LOCK_INDEX = "uq_runs_one_running_root_per_session"
_RUNNING_ROOT = text("parent_run_id IS NULL AND status = 'running'")

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
    history_json: Mapped[JsonObject] = mapped_column(
        JSONB, server_default=text("""'{"messages": []}'::jsonb""")
    )
    model_override: Mapped[str | None] = mapped_column(Text)
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
    sha256: Mapped[str] = mapped_column(CHAR(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
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


class SessionNotFoundError(LookupError):
    pass


class TurnConflictError(RuntimeError):
    pass


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
        session = Session(title=title, model_override=model_override)
        connection.add(session)
    return session


async def get_session_or_raise(db: SessionFactory, session_id: UUID) -> Session:
    async with db() as connection:
        session = await connection.get(Session, session_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} does not exist")
        return session


async def bind_session_sandbox_if_unset(
    db: SessionFactory, session_id: UUID, sandbox_id: str
) -> tuple[str, bool]:
    """Bind a sandbox id only if unset, returning (effective_sandbox_id, won)."""
    async with db.begin() as connection:
        bound = await connection.scalar(
            update(Session)
            .where(Session.id == session_id, Session.daytona_sandbox_id.is_(None))
            .values(daytona_sandbox_id=sandbox_id, updated_at=func.now())
            .returning(Session.daytona_sandbox_id)
        )
        if bound is not None:
            return bound, True
        row = (
            await connection.execute(
                select(Session.daytona_sandbox_id).where(Session.id == session_id)
            )
        ).one_or_none()
        if row is None:
            raise SessionNotFoundError(f"Session {session_id} does not exist")
        existing = row[0]
        if existing is None:
            raise RuntimeError(f"Session {session_id} sandbox binding could not be resolved")
        return existing, False


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


async def finish_run(
    db: SessionFactory,
    run_id: UUID,
    *,
    status: TerminalStatus,
    answer: str | None = None,
    ui_parts: list[JsonObject] | None = None,
    error: str | None = None,
    history_json: JsonObject | None = None,
) -> bool:
    """Commit the terminal result and any root history together. Return false on a late retry."""
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError("finish_run requires a terminal status")

    async with db.begin() as connection:
        result = await connection.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "running")
            .values(
                status=status,
                answer=answer,
                ui_parts=[] if ui_parts is None else ui_parts,
                error=error,
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
                .values(history_json=history_json)
            )
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
        roots = (
            await connection.scalars(
                expired.where(Run.parent_run_id.is_(None), Run.created_at < cutoff).returning(
                    Run.id
                )
            )
        ).all()
        child_condition = Run.created_at < cutoff
        if roots:
            child_condition = child_condition | Run.root_run_id.in_(roots)
        children = await connection.execute(
            expired.where(
                Run.parent_run_id.is_not(None),
                child_condition,
            ).execution_options(synchronize_session=False)
        )
        reaped_children = max(getattr(children, "rowcount", 0) or 0, 0)
        return len(roots) + reaped_children


async def get_session_ui_messages(db: SessionFactory, session_id: UUID) -> list[JsonObject]:
    async with db() as connection:
        runs = await connection.scalars(
            select(Run)
            .where(
                Run.session_id == session_id,
                Run.parent_run_id.is_(None),
                Run.status != "running",
            )
            .order_by(Run.id)
        )
        messages = []
        for run in runs:
            messages.append(
                {
                    "id": f"{run.id}:user",
                    "role": "user",
                    "parts": [{"type": "text", "text": run.prompt}],
                }
            )
            if run.ui_parts:
                messages.append({"id": str(run.id), "role": "assistant", "parts": run.ui_parts})
        return messages


async def upsert_file_record(
    db: SessionFactory,
    session_id: UUID,
    *,
    path: str,
    sha256: str,
    size_bytes: int,
    kind: Literal["input", "artifact"],
) -> File:
    values = {"sha256": sha256, "size_bytes": size_bytes, "kind": kind}
    statement = (
        insert(File)
        .values(session_id=session_id, path=path, **values)
        .on_conflict_do_update(constraint="uq_files_session_path", set_=values)
        .returning(File)
    )
    async with db.begin() as connection:
        return (await connection.scalars(statement)).one()


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
    scope = Memory.session_id.is_(None)
    if session_id is not None:
        scope = scope | (Memory.session_id == session_id)
    approval_filter = () if include_unapproved else (Memory.approved.is_(True),)
    async with db() as connection:
        matches = list(
            await connection.scalars(
                select(Memory)
                .where(scope, *approval_filter, Memory.search_tsv.bool_op("@@")(strict_q))
                .order_by(func.ts_rank(Memory.search_tsv, strict_q).desc(), Memory.id.desc())
                .limit(limit)
            )
        )
        seen_ids = {memory.id for memory in matches}
        if len(matches) < limit:
            relaxed_stmt = (
                select(Memory)
                .where(
                    scope,
                    *approval_filter,
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
            if seen_ids:
                relaxed_stmt = relaxed_stmt.where(Memory.id.not_in(seen_ids))
            matches.extend(await connection.scalars(relaxed_stmt))
        return matches


async def list_approved_session_memories(
    db: SessionFactory, session_id: UUID, *, limit: int = 10
) -> list[Memory]:
    """Return the most recent approved memories belonging to this session."""
    async with db() as connection:
        result = await connection.scalars(
            select(Memory)
            .where(Memory.session_id == session_id, Memory.approved.is_(True))
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
                .where(Memory.id == memory_id, Memory.session_id == session_id)
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


async def update_workspace_settings_overrides(
    db: SessionFactory, changes: dict[str, Any]
) -> dict[str, Any]:
    async with db.begin() as connection:
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
