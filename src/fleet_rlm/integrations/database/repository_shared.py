"""Shared repository helpers and request-context operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import DatabaseManager
from .models_enums import MembershipRole
from .models_identity import Membership, User


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
    ) -> None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": "" if user_id is None else str(user_id)},
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
