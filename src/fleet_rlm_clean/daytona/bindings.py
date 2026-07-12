"""Sandbox binding domain records and async store against clean_sandbox_bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm_clean.daytona.paths import DEFAULT_VOLUME_MOUNT_PATH
from fleet_rlm_clean.daytona.volumes import (
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
)
from fleet_rlm_clean.persistence.models import SandboxBindingRow


@dataclass(frozen=True, slots=True)
class SandboxBinding:
    session_id: UUID
    sandbox_id: str | None
    workspace_id: UUID
    volume_id: str | None
    volume_subpath: str
    mount_path: str
    provider_state: str
    last_verified_at: datetime | None = None


def _validate_binding_fields(binding: SandboxBinding) -> None:
    require_non_zero_workspace_id(binding.workspace_id)
    require_scoped_volume_subpath(binding.volume_subpath, workspace_id=binding.workspace_id)


def _row_to_binding(row: SandboxBindingRow) -> SandboxBinding:
    return SandboxBinding(
        session_id=row.session_id,
        sandbox_id=row.sandbox_id,
        workspace_id=row.workspace_id,
        volume_id=row.volume_id,
        volume_subpath=row.volume_subpath,
        mount_path=row.mount_path,
        provider_state=row.provider_state,
        last_verified_at=row.last_verified_at,
    )


class BindingStore:
    """Persist per-session Sandbox/Volume binding metadata."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        async with self._session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(select(SandboxBindingRow).where(SandboxBindingRow.session_id == session_id))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_binding(row)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        _validate_binding_fields(binding)
        async with self._session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(SandboxBindingRow).where(SandboxBindingRow.session_id == binding.session_id)
            )
            row = result.scalar_one_or_none()
            now = datetime.now(UTC)
            mount_path = binding.mount_path or DEFAULT_VOLUME_MOUNT_PATH
            if row is None:
                row = SandboxBindingRow(
                    id=uuid4(),
                    session_id=binding.session_id,
                    sandbox_id=binding.sandbox_id,
                    workspace_id=binding.workspace_id,
                    volume_id=binding.volume_id,
                    volume_subpath=binding.volume_subpath,
                    mount_path=mount_path,
                    provider_state=binding.provider_state,
                    last_verified_at=binding.last_verified_at or now,
                )
                db.add(row)
            else:
                row.sandbox_id = binding.sandbox_id
                row.workspace_id = binding.workspace_id
                row.volume_id = binding.volume_id
                row.volume_subpath = binding.volume_subpath
                row.mount_path = mount_path
                row.provider_state = binding.provider_state
                row.last_verified_at = binding.last_verified_at or now
            await db.commit()
            return SandboxBinding(
                session_id=row.session_id,
                sandbox_id=row.sandbox_id,
                workspace_id=row.workspace_id,
                volume_id=row.volume_id,
                volume_subpath=row.volume_subpath,
                mount_path=row.mount_path,
                provider_state=row.provider_state,
                last_verified_at=row.last_verified_at,
            )


class InMemoryBindingStore:
    """Test double: no database required."""

    def __init__(self) -> None:
        self._items: dict[UUID, SandboxBinding] = {}

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        return self._items.get(session_id)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        _validate_binding_fields(binding)
        self._items[binding.session_id] = binding
        return binding
