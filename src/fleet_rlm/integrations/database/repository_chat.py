"""Chat domain repository: sessions, turns, runs, steps, artifacts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from .models_enums import (
    ArtifactKind,
    ArtifactProvider,
    ChatSessionStatus,
    ChatTurnStatus,
    ExternalTraceProvider,
    RunStatus,
    RunStepType,
    RunType,
    SandboxProvider,
)
from .models_runs import (
    Artifact,
    ChatSession,
    ChatTurn,
    ExternalTrace,
    Run,
    RunStep,
    TraceFeedback,
)
from .repository_shared import (
    RepositoryContextMixin,
    _coerce_enum,
    _count_from_stmt,
    _utc_now,
)


@dataclass(frozen=True)
class RunCreateRequest:
    tenant_id: uuid.UUID
    created_by_user_id: uuid.UUID | None
    external_run_id: str
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    run_type: RunType = RunType.CHAT_TURN
    status: RunStatus = RunStatus.RUNNING
    model_provider: str | None = None
    model_name: str | None = None
    sandbox_provider: SandboxProvider | None = None
    sandbox_session_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    error_json: dict[str, Any] | None = None
    metrics_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunStepCreateRequest:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    step_index: int
    step_type: RunStepType
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    tool_name: str | None = None
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cost_usd_micros: int | None = None


@dataclass(frozen=True)
class ArtifactCreateRequest:
    tenant_id: uuid.UUID
    kind: ArtifactKind
    uri: str
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    provider: ArtifactProvider = ArtifactProvider.MEMORY
    path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatSessionUpsertRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None
    title: str
    status: ChatSessionStatus = ChatSessionStatus.ACTIVE
    model_provider: str | None = None
    model_name: str | None = None
    active_manifest_path: str | None = None
    session_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatTurnCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    user_message: str
    user_id: uuid.UUID | None = None
    assistant_message: str | None = None
    status: ChatTurnStatus = ChatTurnStatus.COMPLETED
    degraded: bool = False
    model_provider: str | None = None
    model_name: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    error_json: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None


class ChatRepository(RepositoryContextMixin):
    """Chat session, turn, run, step, and artifact operations."""

    async def create_run(self, request: RunCreateRequest) -> Run:
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.created_by_user_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            stmt = insert(Run).values(
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                parent_run_id=request.parent_run_id,
                external_run_id=request.external_run_id,
                run_type=request.run_type,
                created_by_user_id=request.created_by_user_id,
                status=request.status,
                model_provider=request.model_provider,
                model_name=request.model_name,
                sandbox_provider=request.sandbox_provider,
                sandbox_session_id=request.sandbox_session_id,
                error_json=request.error_json,
                metrics_json=request.metrics_json,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[Run.workspace_id, Run.external_run_id],
                set_={
                    "workspace_id": workspace_id,
                    "session_id": request.session_id,
                    "turn_id": request.turn_id,
                    "parent_run_id": request.parent_run_id,
                    "run_type": request.run_type,
                    "created_by_user_id": request.created_by_user_id,
                    "status": request.status,
                    "model_provider": request.model_provider,
                    "model_name": request.model_name,
                    "sandbox_provider": request.sandbox_provider,
                    "sandbox_session_id": request.sandbox_session_id,
                    "error_json": request.error_json,
                    "metrics_json": request.metrics_json,
                    "updated_at": _utc_now(),
                },
            ).returning(Run)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def upsert_chat_session(
        self,
        request: ChatSessionUpsertRequest,
    ) -> ChatSession:
        status = _coerce_enum(request.status, ChatSessionStatus)
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            values: dict[str, object] = {
                "tenant_id": request.tenant_id,
                "workspace_id": workspace_id,
                "user_id": request.user_id,
                "title": request.title,
                "status": status,
                "model_provider": request.model_provider,
                "model_name": request.model_name,
                "active_manifest_path": request.active_manifest_path,
                "metadata_json": request.metadata_json,
                "last_activity_at": _utc_now(),
            }
            if request.session_id is not None:
                values["id"] = request.session_id

            insert_stmt = insert(ChatSession).values(**values)
            if request.session_id is None:
                result = await session.execute(insert_stmt.returning(ChatSession))
                return result.scalar_one()

            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[ChatSession.id],
                set_={
                    "title": request.title,
                    "status": status,
                    "model_provider": request.model_provider,
                    "model_name": request.model_name,
                    "active_manifest_path": request.active_manifest_path,
                    ChatSession.metadata_json: request.metadata_json,
                    "last_activity_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
            ).returning(ChatSession)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def append_chat_turn(self, request: ChatTurnCreateRequest) -> ChatTurn:
        status = _coerce_enum(request.status, ChatTurnStatus)
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            session_stmt = (
                select(ChatSession)
                .where(
                    and_(
                        ChatSession.tenant_id == request.tenant_id,
                        ChatSession.workspace_id == workspace_id,
                        ChatSession.id == request.session_id,
                    )
                )
                .with_for_update()
            )
            session_row = (await session.execute(session_stmt)).scalar_one_or_none()
            if session_row is None:
                raise ValueError(
                    f"Chat session not found for tenant={request.tenant_id} "
                    f"workspace={workspace_id} session={request.session_id}"
                )

            next_turn_index = int(session_row.monotonic_turn_counter)
            session_row.monotonic_turn_counter = next_turn_index + 1
            session_row.last_activity_at = _utc_now()
            session_row.updated_at = _utc_now()

            stmt = (
                insert(ChatTurn)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    run_id=request.run_id,
                    turn_index=next_turn_index,
                    user_message=request.user_message,
                    assistant_message=request.assistant_message,
                    status=status,
                    degraded=request.degraded,
                    model_provider=request.model_provider,
                    model_name=request.model_name,
                    tokens_in=request.tokens_in,
                    tokens_out=request.tokens_out,
                    latency_ms=request.latency_ms,
                    error_json=request.error_json,
                )
                .returning(ChatTurn)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_chat_sessions(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        search: str | None = None,
        status: ChatSessionStatus | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ChatSession], int]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatSession]] = select(ChatSession).where(
                ChatSession.tenant_id == tenant_id
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            if status is not None:
                stmt = stmt.where(ChatSession.status == status)
            else:
                stmt = stmt.where(ChatSession.status == ChatSessionStatus.ACTIVE)
            if search:
                like_pattern = f"%{search}%"
                stmt = stmt.where(
                    or_(
                        ChatSession.title.ilike(like_pattern),
                        ChatSession.metadata_json["external_session_id"]
                        .as_string()
                        .ilike(like_pattern),
                    )
                )
            if created_after is not None:
                stmt = stmt.where(ChatSession.created_at >= created_after)
            if created_before is not None:
                stmt = stmt.where(ChatSession.created_at <= created_before)
            if model_name is not None:
                stmt = stmt.where(ChatSession.model_name == model_name)
            if model_provider is not None:
                stmt = stmt.where(ChatSession.model_provider == model_provider)

            total = await _count_from_stmt(session, stmt)
            items_stmt = (
                stmt.order_by(ChatSession.updated_at.desc()).offset(offset).limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

    async def get_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> ChatSession | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatSession]] = select(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_chat_turns(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatTurn], int]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt: Select[tuple[ChatTurn]] = select(ChatTurn).where(
                and_(
                    ChatTurn.tenant_id == tenant_id,
                    ChatTurn.session_id == session_id,
                )
            )
            if workspace_id is not None:
                stmt = stmt.where(ChatTurn.workspace_id == workspace_id)
            total = await _count_from_stmt(session, stmt)
            items_stmt = stmt.order_by(ChatTurn.turn_index.asc()).offset(offset)
            if limit > 0:
                items_stmt = items_stmt.limit(limit)
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

    async def update_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        title: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> ChatSession | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt = update(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            values: dict[str, object] = {
                "updated_at": _utc_now(),
                "last_activity_at": _utc_now(),
            }
            if title is not None:
                values["title"] = title
            if metadata_json is not None:
                values["metadata_json"] = metadata_json
            stmt = stmt.values(**values).returning(ChatSession)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def archive_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt = update(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            stmt = stmt.values(
                status=ChatSessionStatus.ARCHIVED,
                updated_at=_utc_now(),
                last_activity_at=_utc_now(),
            ).returning(ChatSession.id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def restore_chat_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        """Restore an archived session to active status.

        Returns True if the session was found and restored, False otherwise.
        """
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            stmt = update(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                    ChatSession.status == ChatSessionStatus.ARCHIVED,
                )
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                stmt = stmt.where(ChatSession.workspace_id == workspace_id)
            stmt = stmt.values(
                status=ChatSessionStatus.ACTIVE,
                updated_at=_utc_now(),
                last_activity_at=_utc_now(),
            ).returning(ChatSession.id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def update_run_status(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        status: RunStatus,
        error_json: dict | None = None,
    ) -> Run | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            values: dict[str, object] = {
                "status": status,
                "updated_at": _utc_now(),
            }
            if status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                values["completed_at"] = _utc_now()
            if error_json is not None:
                values["error_json"] = error_json
            stmt = (
                update(Run)
                .where(and_(Run.id == run_id, Run.tenant_id == tenant_id))
                .values(**values)
                .returning(Run)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def append_step(self, request: RunStepCreateRequest) -> RunStep:
        step_type = _coerce_enum(request.step_type, RunStepType)
        async with self._scoped_session(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
        ) as (session, workspace_id):
            stmt = insert(RunStep).values(
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
                run_id=request.run_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                step_index=request.step_index,
                step_type=step_type,
                tool_name=request.tool_name,
                input_json=request.input_json,
                output_json=request.output_json,
                cost_usd_micros=request.cost_usd_micros,
                tokens_in=request.tokens_in,
                tokens_out=request.tokens_out,
                latency_ms=request.latency_ms,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    RunStep.run_id,
                    RunStep.step_index,
                ],
                set_={
                    "step_type": step_type,
                    "session_id": request.session_id,
                    "turn_id": request.turn_id,
                    "tool_name": request.tool_name,
                    "input_json": request.input_json,
                    "output_json": request.output_json,
                    "cost_usd_micros": request.cost_usd_micros,
                    "tokens_in": request.tokens_in,
                    "tokens_out": request.tokens_out,
                    "latency_ms": request.latency_ms,
                    "updated_at": _utc_now(),
                },
            ).returning(RunStep)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def store_artifact(self, request: ArtifactCreateRequest) -> Artifact:
        kind = _coerce_enum(request.kind, ArtifactKind)
        async with self._db.session() as session, session.begin():
            workspace_id = request.workspace_id
            if workspace_id is None and request.run_id is not None:
                run_workspace = await session.execute(
                    select(Run.workspace_id).where(
                        and_(
                            Run.id == request.run_id, Run.tenant_id == request.tenant_id
                        )
                    )
                )
                workspace_id = run_workspace.scalar_one_or_none()
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                workspace_id=workspace_id,
            )
            await self._set_request_context(
                session, request.tenant_id, workspace_id=workspace_id
            )
            stmt = (
                insert(Artifact)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    run_id=request.run_id,
                    step_id=request.step_id,
                    event_id=request.event_id,
                    kind=kind,
                    provider=request.provider,
                    uri=request.uri,
                    path=request.path,
                    mime_type=request.mime_type,
                    size_bytes=request.size_bytes,
                    checksum=request.checksum,
                    metadata_json=request.metadata_json,
                )
                .returning(Artifact)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def get_session_stats(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, object] | None:
        """Return aggregated usage stats for all turns in a session.

        Returns None if the session does not exist or is not owned.
        """
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, user_id, workspace_id)
            session_stmt: Select[tuple[ChatSession]] = select(ChatSession).where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            if user_id is not None:
                session_stmt = session_stmt.where(ChatSession.user_id == user_id)
            if workspace_id is not None:
                session_stmt = session_stmt.where(
                    ChatSession.workspace_id == workspace_id
                )
            session_row = (await session.execute(session_stmt)).scalar_one_or_none()
            if session_row is None:
                return None

            turn_filter = and_(
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.session_id == session_id,
            )
            if workspace_id is not None:
                turn_filter = and_(turn_filter, ChatTurn.workspace_id == workspace_id)

            agg_stmt = select(
                func.coalesce(func.sum(ChatTurn.tokens_in), 0),
                func.coalesce(func.sum(ChatTurn.tokens_out), 0),
                func.coalesce(func.sum(ChatTurn.latency_ms), 0),
            ).where(turn_filter)
            agg_row = (await session.execute(agg_stmt)).one()

            breakdown_stmt = (
                select(
                    func.coalesce(ChatTurn.model_name, "unknown"),
                    func.count(),
                )
                .where(turn_filter)
                .group_by(func.coalesce(ChatTurn.model_name, "unknown"))
            )
            breakdown_rows = (await session.execute(breakdown_stmt)).all()
            model_breakdown = {str(name): int(cnt) for name, cnt in breakdown_rows}

            return {
                "total_tokens_in": int(agg_row[0]),
                "total_tokens_out": int(agg_row[1]),
                "total_latency_ms": int(agg_row[2]),
                "model_breakdown": model_breakdown,
            }

    async def get_run(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Run | None:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = select(Run).where(
                and_(
                    Run.tenant_id == tenant_id,
                    Run.workspace_id == resolved_workspace_id,
                    Run.id == run_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_run_steps(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[RunStep]:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = (
                select(RunStep)
                .where(
                    and_(
                        RunStep.tenant_id == tenant_id,
                        RunStep.workspace_id == resolved_workspace_id,
                        RunStep.run_id == run_id,
                    )
                )
                .order_by(RunStep.step_index.asc())
            )
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def count_run_steps(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
    ) -> int:
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            stmt = (
                select(func.count())
                .select_from(RunStep)
                .where(
                    and_(
                        RunStep.tenant_id == tenant_id,
                        RunStep.workspace_id == resolved_workspace_id,
                        RunStep.run_id == run_id,
                    )
                )
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def get_run_steps_paginated(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunStep], int]:
        """Return run steps with total count in one session round-trip."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=created_by_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            base_filter = and_(
                RunStep.tenant_id == tenant_id,
                RunStep.workspace_id == resolved_workspace_id,
                RunStep.run_id == run_id,
            )
            count_stmt = select(func.count()).select_from(RunStep).where(base_filter)
            total = int((await session.execute(count_stmt)).scalar_one())

            items_stmt = (
                select(RunStep)
                .where(base_filter)
                .order_by(RunStep.step_index.asc())
                .offset(offset)
                .limit(limit)
            )
            items = list((await session.execute(items_stmt)).scalars().all())
            return items, total

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
        """Persist an RLM child trajectory trace to the external_traces table."""
        async with self._scoped_session(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            metadata = dict(payload_json or {})
            if summary_text:
                metadata["summary_text"] = summary_text
            if latency_ms is not None:
                metadata["latency_ms"] = latency_ms
            stmt = (
                insert(ExternalTrace)
                .values(
                    tenant_id=tenant_id,
                    workspace_id=resolved_workspace_id,
                    run_id=run_id,
                    provider=ExternalTraceProvider.MLFLOW,
                    trace_id=trace_id,
                    metadata_json=metadata,
                )
                .on_conflict_do_update(
                    constraint="uq_external_traces_tenant_provider_trace_id",
                    set_={
                        "metadata": metadata,
                        "updated_at": _utc_now(),
                    },
                )
                .returning(ExternalTrace.id)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

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
        provider: ExternalTraceProvider = ExternalTraceProvider.MLFLOW,
        client_request_id: str | None = None,
        run_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Persist human feedback for an external trace.

        Upserts the ``external_traces`` row by (tenant, provider, trace_id) so
        feedback always has a valid FK target, then inserts a ``trace_feedback``
        row. Returns the trace_feedback row id.
        """
        async with self._scoped_session(
            tenant_id=tenant_id,
            user_id=reviewer_user_id,
            workspace_id=workspace_id,
        ) as (session, resolved_workspace_id):
            trace_stmt = (
                insert(ExternalTrace)
                .values(
                    tenant_id=tenant_id,
                    workspace_id=resolved_workspace_id,
                    run_id=run_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    provider=provider,
                    trace_id=trace_id,
                    client_request_id=client_request_id,
                    metadata_json={},
                )
                .on_conflict_do_update(
                    constraint="uq_external_traces_tenant_provider_trace_id",
                    set_={"updated_at": _utc_now()},
                )
                .returning(ExternalTrace.id)
            )
            external_trace_id = (await session.execute(trace_stmt)).scalar_one()

            feedback_stmt = (
                insert(TraceFeedback)
                .values(
                    tenant_id=tenant_id,
                    workspace_id=resolved_workspace_id,
                    external_trace_id=external_trace_id,
                    run_id=run_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    reviewer_user_id=reviewer_user_id,
                    is_correct=is_correct,
                    comment=comment,
                    expected_response=expected_response,
                    metadata_json=metadata_json or {},
                )
                .returning(TraceFeedback.id)
            )
            return (await session.execute(feedback_stmt)).scalar_one()


__all__ = [
    "ArtifactCreateRequest",
    "ChatRepository",
    "ChatSessionUpsertRequest",
    "ChatTurnCreateRequest",
    "RunCreateRequest",
    "RunStepCreateRequest",
]
