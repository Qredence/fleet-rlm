"""Tests for migration 0009, LLMProvider CRUD, heartbeat reaping, rollback stepping, and locking."""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from fleet_rlm import db
from fleet_rlm.config import Settings


def _base_settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "_env_file": None,
        "database_url": "postgresql://primary-host/neondb",
        "database_direct_url": "postgresql://primary-host/neondb",
        "daytona_api_key": "offline",
        "daytona_image": "offline",
        "llm_model": "openai/gpt-4o-mini",
        "sub_llm_model": "openai/gpt-4o-mini",
        "llm_api_key": "offline",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def _compile_pg(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _make_integrity_error(constraint_name: str) -> IntegrityError:
    orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint_name))
    return IntegrityError("INSERT ...", {}, orig)  # type: ignore[arg-type]


def test_new_config_settings_defaults_and_validation() -> None:
    settings = _base_settings()
    assert settings.credentials_encryption_key is None
    assert settings.allow_private_llm_endpoints is False
    assert settings.allow_local_interpreter is False
    assert settings.daytona_dataset_volume == "fleet-eval-datasets"
    assert settings.optimization_heartbeat_stale_seconds == 300

    normalized = _base_settings(credentials_encryption_key="   ")
    assert normalized.credentials_encryption_key is None

    with pytest.raises(ValidationError):
        _base_settings(daytona_dataset_volume="   ")
    with pytest.raises(ValidationError):
        _base_settings(optimization_heartbeat_stale_seconds=5)


def test_alembic_0009_migration_structure_and_orm_metadata() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0009_optimization_extensions.py"
    )
    spec = importlib.util.spec_from_file_location("m0009", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0009_optimization_extensions"
    assert module.down_revision == "0008_optimization_jobs"
    assert callable(module.upgrade)
    assert callable(module.downgrade)

    # Verify ORM metadata contains all new tables, columns, FKs, and constraints
    tables = db.Base.metadata.tables
    assert "llm_providers" in tables
    lp_cols = set(tables["llm_providers"].columns.keys())
    assert lp_cols == {
        "id",
        "name",
        "dialect",
        "api_mode",
        "base_url",
        "api_key_ciphertext",
        "api_key_hint",
        "default_model",
        "cached_models_json",
        "last_tested_at",
        "created_at",
        "updated_at",
    }

    ds_cols = set(tables["optimization_datasets"].columns.keys())
    assert {"storage_backend", "storage_volume"} <= ds_cols

    job_cols = set(tables["optimization_jobs"].columns.keys())
    assert {"heartbeat_at", "decision_provider_id"} <= job_cols
    job_fks = {
        (fk.column.table.name, fk.ondelete)
        for fk in tables["optimization_jobs"].foreign_keys
        if fk.parent.name == "decision_provider_id"
    }
    assert job_fks == {("llm_providers", "SET NULL")}

    promo_cols = set(tables["optimization_promotions"].columns.keys())
    assert {"decision_provider_id", "program_kind"} <= promo_cols
    promo_fks = {
        (fk.column.table.name, fk.ondelete)
        for fk in tables["optimization_promotions"].foreign_keys
        if fk.parent.name == "decision_provider_id"
    }
    assert promo_fks == {("llm_providers", "SET NULL")}


class _FakeAsyncSessionFactory:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __call__(self):
        return self._ctx()

    def begin(self):
        return self._ctx()

    @asynccontextmanager
    async def _ctx(self):
        yield self._conn


@pytest.mark.asyncio
async def test_reap_stale_optimization_jobs_uses_db_now_and_heartbeat_coalesce() -> None:
    executed_sql: list[str] = []

    class Conn:
        async def execute(self, stmt: Any):
            executed_sql.append(_compile_pg(stmt))
            return SimpleNamespace(rowcount=2)

    factory = _FakeAsyncSessionFactory(Conn())
    reaped = await db.reap_stale_optimization_jobs(factory, 300)  # type: ignore[arg-type]
    assert reaped == 2
    assert len(executed_sql) == 1
    sql = executed_sql[0].lower()
    expected_coalesce = (
        "coalesce(optimization_jobs.heartbeat_at, "
        "optimization_jobs.started_at, optimization_jobs.created_at)"
    )
    assert expected_coalesce in sql
    assert "now() -" in sql


@pytest.mark.asyncio
async def test_touch_heartbeat_and_progress_update_refresh_heartbeat_at() -> None:
    executed_sql: list[str] = []

    class Conn:
        async def execute(self, stmt: Any):
            executed_sql.append(_compile_pg(stmt))
            return SimpleNamespace(rowcount=1)

        async def scalar(self, stmt: Any):
            executed_sql.append(_compile_pg(stmt))
            return SimpleNamespace(id=uuid4())

    factory = _FakeAsyncSessionFactory(Conn())
    assert await db.touch_optimization_job_heartbeat(factory, uuid4()) is True  # type: ignore[arg-type]
    assert "heartbeat_at=now()" in executed_sql[0].lower()

    await db.update_optimization_job_progress(factory, uuid4(), stage="eval")  # type: ignore[arg-type]
    assert "heartbeat_at=now()" in executed_sql[1].lower()


@pytest.mark.asyncio
async def test_create_optimization_job_locks_dataset_and_provider_for_key_share() -> None:
    ds_id = uuid4()
    prov_id = uuid4()
    get_calls: list[tuple[Any, UUID, Any]] = []
    added: list[Any] = []

    class Conn:
        async def get(self, model: Any, identity: UUID, *, with_for_update: Any = None):
            get_calls.append((model, identity, with_for_update))
            if model is db.OptimizationDataset and identity == ds_id:
                return SimpleNamespace(id=ds_id)
            if model is db.LLMProvider and identity == prov_id:
                return SimpleNamespace(id=prov_id)
            return None

        def add(self, obj: Any) -> None:
            added.append(obj)

    factory = _FakeAsyncSessionFactory(Conn())
    job = await db.create_optimization_job(
        factory,  # type: ignore[arg-type]
        target="skills",
        program_kind=None,
        dataset_source="custom",
        dataset_name="custom.jsonl",
        dataset_id=ds_id,
        config_json={},
        mlflow_experiment="exp",
        decision_provider_id=prov_id,
    )
    assert job.dataset_id == ds_id
    assert job.decision_provider_id == prov_id
    assert get_calls == [
        (db.OptimizationDataset, ds_id, {"key_share": True}),
        (db.LLMProvider, prov_id, {"key_share": True}),
    ]

    # Missing dataset raises LookupError
    with pytest.raises(LookupError, match="dataset"):
        await db.create_optimization_job(
            factory,  # type: ignore[arg-type]
            target="skills",
            program_kind=None,
            dataset_source="custom",
            dataset_name="missing.jsonl",
            dataset_id=uuid4(),
            config_json={},
            mlflow_experiment="exp",
        )


@pytest.mark.asyncio
async def test_rollback_steps_strictly_backward_and_promote_maps_conflict() -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = t1 + timedelta(minutes=5)
    t3 = t2 + timedelta(minutes=5)
    p1 = db.OptimizationPromotion(
        id=UUID("01900000-0000-7000-8000-000000000001"),
        target="skills",
        job_id=uuid4(),
        artifact_object_key="k1",
        artifact_sha256="a" * 64,
        artifact_size_bytes=10,
        active=False,
        promoted_at=t1,
    )
    p2 = db.OptimizationPromotion(
        id=UUID("01900000-0000-7000-8000-000000000002"),
        target="skills",
        job_id=uuid4(),
        artifact_object_key="k2",
        artifact_sha256="b" * 64,
        artifact_size_bytes=10,
        active=False,
        promoted_at=t2,
    )
    p3 = db.OptimizationPromotion(
        id=UUID("01900000-0000-7000-8000-000000000003"),
        target="skills",
        job_id=uuid4(),
        artifact_object_key="k3",
        artifact_sha256="c" * 64,
        artifact_size_bytes=10,
        active=True,
        promoted_at=t3,
    )
    promos = [p1, p2, p3]
    seen_rollback_sql: list[str] = []

    class RollbackConn:
        def __init__(self) -> None:
            self._current: db.OptimizationPromotion | None = None

        async def flush(self) -> None:
            return None

        async def scalar(self, stmt: Any):
            sql = _compile_pg(stmt)
            seen_rollback_sql.append(sql)
            if "active IS true" in sql or "active = true" in sql:
                self._current = next((p for p in promos if p.active), None)
                return self._current
            assert "(optimization_promotions.promoted_at, optimization_promotions.id) <" in sql
            assert (
                "ORDER BY optimization_promotions.promoted_at DESC, optimization_promotions.id DESC"
            ) in sql
            assert self._current is not None
            earlier = [
                p
                for p in promos
                if not p.active
                and (p.promoted_at, p.id) < (self._current.promoted_at, self._current.id)
            ]
            earlier.sort(key=lambda p: (p.promoted_at, p.id), reverse=True)
            return earlier[0] if earlier else None

    factory = _FakeAsyncSessionFactory(RollbackConn())
    # P3 -> P2 -> P1 -> None (never toggles back to P3!)
    first = await db.rollback_optimization_target(factory, "skills")  # type: ignore[arg-type]
    assert first is p2 and p2.active and not p3.active
    second = await db.rollback_optimization_target(factory, "skills")  # type: ignore[arg-type]
    assert second is p1 and p1.active and not p2.active and not p3.active
    third = await db.rollback_optimization_target(factory, "skills")  # type: ignore[arg-type]
    assert third is None and not p1.active and not p2.active and not p3.active

    # Concurrent promotion race maps IntegrityError on active unique index -> ConflictError
    class RaceConn:
        async def scalars(self, _stmt: Any):
            return SimpleNamespace(all=lambda: [])

        async def flush(self) -> None:
            return None

        def add(self, _obj: Any) -> None:
            raise _make_integrity_error("uq_optimization_promotions_one_active_per_target")

    race_factory = _FakeAsyncSessionFactory(RaceConn())
    with pytest.raises(db.OptimizationConflictError, match="concurrent promotion"):
        await db.promote_optimization_artifact(
            race_factory,  # type: ignore[arg-type]
            target="rlm",
            job_id=uuid4(),
            artifact_object_key="k",
            artifact_sha256="d" * 64,
            artifact_size_bytes=5,
            resolved_decision_model=None,
            program_kind="rlm",
        )


@pytest.mark.asyncio
async def test_llm_provider_delete_conflict_checks_and_crud() -> None:
    provider_id = uuid4()
    provider = db.LLMProvider(id=provider_id, name="OpenAI Prod", dialect="openai", api_mode="auto")

    # 1. Blocked by active promotion
    class PromoRefConn:
        async def get(self, model: Any, identity: UUID, *, with_for_update: Any = None):
            assert with_for_update is True
            return provider if model is db.LLMProvider and identity == provider_id else None

        async def scalar(self, _stmt: Any):
            return uuid4()

        delete = AsyncMock()

    with pytest.raises(db.OptimizationConflictError, match="active optimization promotion"):
        await db.delete_llm_provider(_FakeAsyncSessionFactory(PromoRefConn()), provider_id)  # type: ignore[arg-type]

    # 2. Blocked by active job (via config_json role binding)
    active_job = SimpleNamespace(
        id=uuid4(),
        decision_provider_id=None,
        config_json={"root_lm": {"provider_id": str(provider_id), "model": "gpt-4o"}},
    )

    class JobRefConn:
        async def get(self, model: Any, identity: UUID, *, with_for_update: Any = None):
            return provider if model is db.LLMProvider and identity == provider_id else None

        async def scalar(self, _stmt: Any):
            return None

        async def scalars(self, _stmt: Any):
            return SimpleNamespace(all=lambda: [active_job])

        delete = AsyncMock()

    with pytest.raises(db.OptimizationConflictError, match="active optimization job"):
        await db.delete_llm_provider(_FakeAsyncSessionFactory(JobRefConn()), provider_id)  # type: ignore[arg-type]

    # 3. Succeeds when unreferenced
    delete_mock = AsyncMock()

    class FreeConn:
        async def get(self, model: Any, identity: UUID, *, with_for_update: Any = None):
            return provider if model is db.LLMProvider and identity == provider_id else None

        async def scalar(self, _stmt: Any):
            return None

        async def scalars(self, _stmt: Any):
            return SimpleNamespace(all=lambda: [])

        delete = delete_mock

    deleted = await db.delete_llm_provider(_FakeAsyncSessionFactory(FreeConn()), provider_id)  # type: ignore[arg-type]
    assert deleted is provider
    delete_mock.assert_awaited_once_with(provider)

    # 4. Duplicate provider name maps to OptimizationConflictError
    class DupConn:
        def add(self, _obj: Any) -> None:
            raise _make_integrity_error("uq_llm_providers_name")

    with pytest.raises(db.OptimizationConflictError, match="already exists"):
        await db.create_llm_provider(
            _FakeAsyncSessionFactory(DupConn()),  # type: ignore[arg-type]
            name="OpenAI Prod",
            dialect="openai",
        )
