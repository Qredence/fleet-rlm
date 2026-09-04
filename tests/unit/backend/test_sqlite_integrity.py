"""SQLite relational-integrity and local-development policy contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.persistence.database import (
    _SQLITE_BUSY_TIMEOUT_MS,
    assert_sqlite_foreign_keys,
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.persistence.models import (
    ArtifactRow,
    AttachmentRow,
    RunRow,
    SandboxBindingRow,
    SessionRow,
    TurnRow,
    UserRow,
    WorkspaceRow,
)


async def _assert_integrity_error(factory: async_sessionmaker[AsyncSession], row: object) -> None:
    async with factory() as db:
        db.add(row)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_sqlite_enforces_foreign_keys_and_reports_no_valid_lineage_violations() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        await assert_sqlite_foreign_keys(engine)
        factory = create_session_factory(engine)
        user_id, workspace_id, session_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=user_id),
                    WorkspaceRow(id=workspace_id),
                    SessionRow(id=session_id, user_id=user_id, workspace_id=workspace_id, title="integrity"),
                    RunRow(
                        id=run_id,
                        session_id=session_id,
                        status="running",
                        idempotency_key="valid",
                        input_fingerprint="a" * 64,
                        base_checkpoint_version=0,
                        claim_owner="owner",
                        claim_heartbeat_at=datetime.now(UTC),
                    ),
                    TurnRow(
                        session_id=session_id,
                        run_id=run_id,
                        sequence=1,
                        role="user",
                        user_input_json={"text": "valid"},
                    ),
                    AttachmentRow(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        filename="valid.txt",
                        byte_size=0,
                        checksum_sha256="b" * 64,
                        storage_ref="attachments/valid",
                    ),
                    ArtifactRow(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=session_id,
                        run_id=run_id,
                        kind="text",
                        byte_size=0,
                        checksum_sha256="c" * 64,
                        storage_ref="artifacts/valid",
                    ),
                    SandboxBindingRow(
                        session_id=session_id,
                        workspace_id=workspace_id,
                        volume_subpath=f"workspaces/{workspace_id}",
                        provider_state="missing",
                    ),
                )
            )
        async with engine.connect() as connection:
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all() == []

        await _assert_integrity_error(
            factory,
            RunRow(
                session_id=uuid4(),
                status="running",
                idempotency_key="bad-run",
                input_fingerprint="d" * 64,
                base_checkpoint_version=0,
                claim_owner="owner",
                claim_heartbeat_at=datetime.now(UTC),
            ),
        )
        await _assert_integrity_error(
            factory,
            TurnRow(
                session_id=session_id,
                run_id=uuid4(),
                sequence=2,
                role="user",
                user_input_json={"text": "invalid run"},
            ),
        )
        await _assert_integrity_error(
            factory,
            ArtifactRow(
                workspace_id=uuid4(),
                user_id=uuid4(),
                session_id=uuid4(),
                run_id=uuid4(),
                kind="text",
                byte_size=0,
                checksum_sha256="e" * 64,
                storage_ref="artifacts/invalid",
            ),
        )
        await _assert_integrity_error(
            factory,
            AttachmentRow(
                workspace_id=uuid4(),
                user_id=uuid4(),
                filename="invalid.txt",
                byte_size=0,
                checksum_sha256="f" * 64,
                storage_ref="attachments/invalid",
            ),
        )
        await _assert_integrity_error(
            factory,
            SandboxBindingRow(
                session_id=session_id,
                workspace_id=uuid4(),
                volume_subpath="workspaces/invalid",
                provider_state="missing",
            ),
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_backed_sqlite_uses_bounded_busy_timeout_and_wal(tmp_path) -> None:
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'fleet.sqlite3'}")
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert await connection.scalar(text("PRAGMA busy_timeout")) == _SQLITE_BUSY_TIMEOUT_MS
            assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
    finally:
        await engine.dispose()
