"""Focused tests for Postgres-backed LLM profile store behavior."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm.integrations.database.models_llm_profiles import LlmRoleBinding
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.llm_profiles.store import ROLE_NAMES, PostgresLlmProfileStore


class _AsyncContext(AbstractAsyncContextManager[Any]):
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: object) -> bool:
        _ = args
        return False


class _RowsResult:
    def __init__(self, rows: list[LlmRoleBinding]) -> None:
        self._rows = rows

    def scalars(self) -> "_RowsResult":
        return self

    def all(self) -> list[LlmRoleBinding]:
        return self._rows


class _Session:
    def __init__(self, rows: list[LlmRoleBinding]) -> None:
        self._rows = rows

    def begin(self) -> _AsyncContext:
        return _AsyncContext(self)

    async def execute(self, _stmt: object) -> _RowsResult:
        return _RowsResult(self._rows)


class _DbManager:
    def __init__(self, row_sets: list[list[LlmRoleBinding]]) -> None:
        self._row_sets = list(row_sets)
        self.session_count = 0

    def session(self) -> _AsyncContext:
        self.session_count += 1
        rows = self._row_sets.pop(0)
        return _AsyncContext(_Session(rows))


class _Store(PostgresLlmProfileStore):
    def __init__(self, db_manager: _DbManager, *, identity: IdentityUpsertResult) -> None:
        super().__init__(db_manager, identity=identity)  # type: ignore[arg-type]
        self.ensure_default_bindings_calls = 0

    async def _set_request_context(self, session: object) -> IdentityUpsertResult:
        _ = session
        return self._require_identity()

    async def _ensure_default_bindings(self, session: object) -> None:
        _ = session
        self.ensure_default_bindings_calls += 1


def _identity() -> IdentityUpsertResult:
    return IdentityUpsertResult(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
    )


def _binding(identity: IdentityUpsertResult, role: str) -> LlmRoleBinding:
    return LlmRoleBinding(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        role=role,
        profile_id=None,
        model_id="",
    )


@pytest.mark.asyncio
async def test_list_role_bindings_does_not_write_when_all_roles_exist() -> None:
    identity = _identity()
    rows = [_binding(identity, role) for role in ROLE_NAMES]
    db_manager = _DbManager([rows])
    store = _Store(db_manager, identity=identity)

    records = await store.list_role_bindings()

    assert {record.role for record in records} == set(ROLE_NAMES)
    assert store.ensure_default_bindings_calls == 0
    assert db_manager.session_count == 1


@pytest.mark.asyncio
async def test_list_role_bindings_repairs_missing_default_roles_once() -> None:
    identity = _identity()
    partial = [_binding(identity, ROLE_NAMES[0])]
    complete = [_binding(identity, role) for role in ROLE_NAMES]
    db_manager = _DbManager([partial, complete])
    store = _Store(db_manager, identity=identity)

    records = await store.list_role_bindings()

    assert {record.role for record in records} == set(ROLE_NAMES)
    assert store.ensure_default_bindings_calls == 1
    assert db_manager.session_count == 2
