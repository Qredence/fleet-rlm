"""SQLAlchemy Sandbox binding store adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.files.volume_paths import DEFAULT_VOLUME_MOUNT_PATH
from fleet_rlm.persistence.models import SandboxBindingRow
from fleet_rlm.runtime.bindings import SandboxBinding, validate_sandbox_binding


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


class SqlAlchemySandboxBindingStore:
    """Persist per-session Sandbox/Volume binding metadata in SQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        async with self._session_factory() as db:
            result = await db.execute(select(SandboxBindingRow).where(SandboxBindingRow.session_id == session_id))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_binding(row)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        async with self._session_factory() as db:
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
            return _row_to_binding(row)


__all__ = ["SqlAlchemySandboxBindingStore"]
