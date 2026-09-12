"""SQLAlchemy Sandbox binding store adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH
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
        generation=row.generation,
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

    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None:
        """Read one binding only when its Workspace scope also matches."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(SandboxBindingRow).where(
                    SandboxBindingRow.session_id == session_id,
                    SandboxBindingRow.workspace_id == workspace_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_binding(row)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        try:
            return await self._write_binding(binding)
        except IntegrityError:
            # Two racing first writes both observed no row; the unique
            # session_id constraint rejected the loser. Retry once so the
            # loser lands as an update of the winner's committed row.
            return await self._write_binding(binding)

    async def _write_binding(self, binding: SandboxBinding) -> SandboxBinding:
        async with self._session_factory() as db:
            result = await db.execute(
                # Serialize generation checks on PostgreSQL so a stale
                # recovery writer cannot race a replacement update. SQLite
                # ignores FOR UPDATE and remains suitable for local tests.
                select(SandboxBindingRow).where(SandboxBindingRow.session_id == binding.session_id).with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is not None and row.workspace_id != binding.workspace_id:
                # Session ids are not tenant keys. Never let a caller from a
                # different Workspace overwrite the existing provider fence.
                raise ValueError("sandbox binding workspace scope mismatch")
            if row is not None and binding.generation < row.generation:
                raise ValueError("stale sandbox binding generation")
            if row is not None and binding.generation == row.generation and binding.sandbox_id != row.sandbox_id:
                raise ValueError("conflicting sandbox binding identity for generation")
            if (
                row is not None
                and binding.generation == row.generation
                and binding.sandbox_id == row.sandbox_id
                and row.provider_state != "running"
                and binding.provider_state == "running"
            ):
                raise ValueError("stale running sandbox binding generation")
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
                    generation=binding.generation,
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
                row.generation = binding.generation
            await db.commit()
            return _row_to_binding(row)

    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        """Persist a replacement while allocating its generation under lock.

        The caller may have read an older binding before provider work began;
        generation allocation therefore must happen in the same locked
        transaction as the replacement write. A unique-key race on the first
        insert is retried so the loser observes the committed row and advances
        from its generation rather than overwriting it.
        """
        validate_sandbox_binding(binding)
        try:
            return await self._replace_with_next_generation(binding)
        except IntegrityError:
            return await self._replace_with_next_generation(binding)

    async def _replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        async with self._session_factory() as db:
            result = await db.execute(
                select(SandboxBindingRow).where(SandboxBindingRow.session_id == binding.session_id).with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is not None and row.workspace_id != binding.workspace_id:
                raise ValueError("sandbox binding workspace scope mismatch")
            if row is None:
                generation = binding.generation
            elif row.sandbox_id == binding.sandbox_id and row.provider_state == "running":
                generation = row.generation
            else:
                generation = row.generation + 1
            candidate = replace(binding, generation=generation)
            now = datetime.now(UTC)
            mount_path = candidate.mount_path or DEFAULT_VOLUME_MOUNT_PATH
            if row is None:
                row = SandboxBindingRow(
                    id=uuid4(),
                    session_id=candidate.session_id,
                    sandbox_id=candidate.sandbox_id,
                    workspace_id=candidate.workspace_id,
                    volume_id=candidate.volume_id,
                    volume_subpath=candidate.volume_subpath,
                    mount_path=mount_path,
                    provider_state=candidate.provider_state,
                    last_verified_at=candidate.last_verified_at or now,
                    generation=candidate.generation,
                )
                db.add(row)
            else:
                row.sandbox_id = candidate.sandbox_id
                row.workspace_id = candidate.workspace_id
                row.volume_id = candidate.volume_id
                row.volume_subpath = candidate.volume_subpath
                row.mount_path = mount_path
                row.provider_state = candidate.provider_state
                row.last_verified_at = candidate.last_verified_at or now
                row.generation = candidate.generation
            await db.commit()
            return _row_to_binding(row)


__all__ = ["SqlAlchemySandboxBindingStore"]
