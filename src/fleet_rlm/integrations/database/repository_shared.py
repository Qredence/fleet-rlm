"""Shared repository helpers and request-context operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import DatabaseManager
from .models_enums import MembershipRole, WorkspaceRole
from .models_identity import (
    Membership,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRuntimeSetting,
)

_DEFAULT_WORKSPACE_SLUG = "default"
_DEFAULT_WORKSPACE_NAME = "Default Workspace"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _RepositoryState:
    _db: DatabaseManager


class RepositoryContextMixin(_RepositoryState):
    async def _set_request_context(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID | str,
        user_id: uuid.UUID | str | None = None,
        workspace_id: uuid.UUID | str | None = None,
    ) -> None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": "" if user_id is None else str(user_id)},
        )
        await session.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": "" if workspace_id is None else str(workspace_id)},
        )

    async def _upsert_user_in_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        insert_stmt = insert(User).values(
            tenant_id=tenant_id,
            entra_user_id=entra_user_id,
            email=email,
            full_name=full_name,
            is_active=is_active,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[User.tenant_id, User.entra_user_id],
            set_={
                "email": func.coalesce(insert_stmt.excluded.email, User.email),
                "full_name": func.coalesce(
                    insert_stmt.excluded.full_name,
                    User.full_name,
                ),
                "is_active": is_active,
                "updated_at": _utc_now(),
            },
        ).returning(User)
        result = await session.execute(stmt)
        return result.scalar_one()

    async def _ensure_membership_in_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        membership_role: MembershipRole = MembershipRole.MEMBER,
    ) -> Membership:
        membership_stmt = insert(Membership).values(
            tenant_id=tenant_id,
            user_id=user_id,
            role=membership_role,
            is_default=True,
        )
        membership_stmt = membership_stmt.on_conflict_do_nothing(
            index_elements=[Membership.tenant_id, Membership.user_id]
        ).returning(Membership)
        created = (await session.execute(membership_stmt)).scalar_one_or_none()
        if created is not None:
            return created

        existing = await session.execute(
            select(Membership).where(
                and_(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == user_id,
                )
            )
        )
        return existing.scalar_one()

    async def _ensure_default_workspace_in_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Workspace:
        workspace_insert = insert(Workspace).values(
            tenant_id=tenant_id,
            slug=_DEFAULT_WORKSPACE_SLUG,
            display_name=_DEFAULT_WORKSPACE_NAME,
            created_by_user_id=user_id,
        )
        workspace_stmt = workspace_insert.on_conflict_do_update(
            index_elements=[Workspace.tenant_id, Workspace.slug],
            set_={
                "updated_at": _utc_now(),
                "created_by_user_id": func.coalesce(
                    Workspace.created_by_user_id,
                    workspace_insert.excluded.created_by_user_id,
                ),
            },
        ).returning(Workspace)
        workspace = (await session.execute(workspace_stmt)).scalar_one()

        runtime_insert = insert(WorkspaceRuntimeSetting).values(
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            updated_by_user_id=user_id,
            settings_json={},
        )
        await session.execute(
            runtime_insert.on_conflict_do_update(
                index_elements=[WorkspaceRuntimeSetting.workspace_id],
                set_={
                    "updated_at": _utc_now(),
                    "updated_by_user_id": func.coalesce(
                        runtime_insert.excluded.updated_by_user_id,
                        WorkspaceRuntimeSetting.updated_by_user_id,
                    ),
                },
            )
        )

        if user_id is None:
            return workspace

        workspace_membership_insert = insert(WorkspaceMembership).values(
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            user_id=user_id,
            role=WorkspaceRole.OWNER,
            is_default=True,
        )
        await session.execute(
            workspace_membership_insert.on_conflict_do_update(
                index_elements=[
                    WorkspaceMembership.workspace_id,
                    WorkspaceMembership.user_id,
                ],
                set_={
                    "is_default": True,
                    "updated_at": _utc_now(),
                },
            )
        )
        return workspace

    async def _resolve_workspace_id_in_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if workspace_id is not None:
            return workspace_id

        if user_id is not None:
            membership_result = await session.execute(
                select(WorkspaceMembership.workspace_id).where(
                    and_(
                        WorkspaceMembership.tenant_id == tenant_id,
                        WorkspaceMembership.user_id == user_id,
                        WorkspaceMembership.is_default.is_(True),
                    )
                )
            )
            membership_workspace_id = membership_result.scalar_one_or_none()
            if membership_workspace_id is not None:
                return membership_workspace_id

        workspace_result = await session.execute(
            select(Workspace.id).where(
                and_(
                    Workspace.tenant_id == tenant_id,
                    Workspace.slug == _DEFAULT_WORKSPACE_SLUG,
                )
            )
        )
        workspace_id_value = workspace_result.scalar_one_or_none()
        if workspace_id_value is not None:
            if user_id is None:
                return workspace_id_value
            workspace = await self._ensure_default_workspace_in_session(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            return workspace.id

        workspace = await self._ensure_default_workspace_in_session(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        return workspace.id
