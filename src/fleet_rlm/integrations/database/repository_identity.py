"""Identity-oriented repository operations."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert

from .models_enums import MembershipRole, TenantStatus
from .models_identity import Tenant, User
from .repository_shared import RepositoryContextMixin, _utc_now
from .types import ControlPlaneIdentityResolution, IdentityUpsertResult


class RepositoryIdentityMixin(RepositoryContextMixin):
    async def upsert_tenant(
        self,
        *,
        entra_tenant_id: str,
        slug: str | None = None,
        display_name: str | None = None,
        domain: str | None = None,
    ) -> Tenant:
        async with self._db.session() as session, session.begin():
            insert_stmt = insert(Tenant).values(
                entra_tenant_id=entra_tenant_id,
                slug=slug,
                display_name=display_name,
                domain=domain,
            )
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[Tenant.entra_tenant_id],
                set_={
                    "slug": func.coalesce(insert_stmt.excluded.slug, Tenant.slug),
                    "display_name": func.coalesce(
                        insert_stmt.excluded.display_name,
                        Tenant.display_name,
                    ),
                    "domain": func.coalesce(
                        insert_stmt.excluded.domain,
                        Tenant.domain,
                    ),
                    "updated_at": _utc_now(),
                },
            ).returning(Tenant)
            row = await session.execute(stmt)
            return row.scalar_one()

    async def upsert_user(
        self,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
        create_membership: bool = True,
        membership_role: MembershipRole = MembershipRole.MEMBER,
    ) -> User:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            user = await self._upsert_user_in_session(
                session,
                tenant_id=tenant_id,
                entra_user_id=entra_user_id,
                email=email,
                full_name=full_name,
                is_active=is_active,
            )

            if create_membership:
                await self._set_request_context(session, tenant_id, user.id)
                await self._ensure_membership_in_session(
                    session,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    membership_role=membership_role,
                )

            return user

    async def upsert_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
    ) -> IdentityUpsertResult:
        tenant = await self.upsert_tenant(
            entra_tenant_id=entra_tenant_id,
            display_name=entra_tenant_id,
        )
        user = await self.upsert_user(
            tenant_id=tenant.id,
            entra_user_id=entra_user_id,
            email=email,
            full_name=full_name,
        )
        return IdentityUpsertResult(
            tenant_id=tenant.id,
            user_id=user.id,
            tenant_status=tenant.status,
        )

    async def resolve_tenant_by_entra_claim(
        self,
        *,
        entra_tenant_id: str,
    ) -> Tenant | None:
        async with self._db.session() as session, session.begin():
            stmt = select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def resolve_control_plane_identity(
        self,
        *,
        entra_tenant_id: str,
        entra_user_id: str,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
        membership_role: MembershipRole = MembershipRole.MEMBER,
    ) -> ControlPlaneIdentityResolution | None:
        async with self._db.session() as session, session.begin():
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.entra_tenant_id == entra_tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if tenant is None:
                return None
            if tenant.status != TenantStatus.ACTIVE:
                return ControlPlaneIdentityResolution(
                    tenant_id=tenant.id,
                    tenant_status=tenant.status,
                )

            await self._set_request_context(session, tenant.id)
            user = await self._upsert_user_in_session(
                session,
                tenant_id=tenant.id,
                entra_user_id=entra_user_id,
                email=email,
                full_name=full_name,
                is_active=is_active,
            )
            await self._set_request_context(session, tenant.id, user.id)
            membership = await self._ensure_membership_in_session(
                session,
                tenant_id=tenant.id,
                user_id=user.id,
                membership_role=membership_role,
            )
            return ControlPlaneIdentityResolution(
                tenant_id=tenant.id,
                tenant_status=tenant.status,
                user_id=user.id,
                membership_role=membership.role,
            )

    async def resolve_user_by_entra_claim(
        self,
        *,
        tenant_id: uuid.UUID,
        entra_user_id: str,
    ) -> User | None:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            stmt = select(User).where(
                and_(
                    User.tenant_id == tenant_id,
                    User.entra_user_id == entra_user_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
