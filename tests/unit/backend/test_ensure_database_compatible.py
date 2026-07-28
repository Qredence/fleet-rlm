"""Unit tests for the shared fail-closed compatibility gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.persistence import database
from fleet_rlm.persistence.database import (
    DatabaseCompatibilityError,
    DatabaseConnectionError,
    ensure_database_compatible,
)


@pytest.mark.asyncio
async def test_revision_mismatch_is_wrapped_with_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise DatabaseCompatibilityError("database revision does not match Alembic head")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseCompatibilityError) as error:
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))

    assert str(error.value) == "Fleet database is not at Alembic head; run `uv run python scripts/db_init.py`"


@pytest.mark.asyncio
async def test_connection_error_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise DatabaseConnectionError("fleet database compatibility could not be verified")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseConnectionError, match="could not be verified"):
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))


@pytest.mark.asyncio
async def test_unexpected_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise OSError("could not read /secret/path/alembic.ini")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseConnectionError) as error:
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))

    assert str(error.value) == "Fleet database compatibility could not be verified"
    assert "/secret/path" not in str(error.value)
    assert isinstance(error.value.__cause__, OSError)
