"""Tests for the Optimization Control Plane REST, SSE, provider registry, volume, and runner."""

import asyncio
import hashlib
import importlib
import json
import logging
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import dspy
import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from pydantic import SecretStr

from fleet_rlm import agent, api, db, interpreter, llm_registry, opt_datasets, opt_runner
from fleet_rlm.config import Settings
from fleet_rlm.storage import ObjectStorage, StorageError

_AUTH = {"Authorization": "Bearer opt-token"}
_FERNET_KEY = Fernet.generate_key().decode("ascii")


def _valid_memory_jsonl() -> bytes:
    rows = [
        {
            "task": "Remember timezone UTC",
            "worth_saving": True,
            "action": "insert",
            "scope": "global",
            "lineage": "tz-1",
            "split": "train",
        },
        {
            "task": "Hello there",
            "worth_saving": False,
            "action": "skip",
            "scope": "session",
            "lineage": "greet-1",
            "split": "holdout",
        },
    ]
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")


class MemoryOptStore:
    """In-memory DB adapter mimicking Postgres optimization & LLM provider tables."""

    def __init__(self) -> None:
        self.datasets: dict[UUID, db.OptimizationDataset] = {}
        self.jobs: dict[UUID, db.OptimizationJob] = {}
        self.promotions: list[db.OptimizationPromotion] = []
        self.providers: dict[UUID, db.LLMProvider] = {}
        self.overrides: dict[str, object] = {}
        self.heartbeat_touches: list[UUID] = []
        self._promo_seq = 0

    async def get_overrides(self, _sessionmaker) -> dict[str, object]:
        return dict(self.overrides)

    async def update_overrides(
        self, _sessionmaker, changes: dict[str, object]
    ) -> dict[str, object]:
        for field, value in changes.items():
            if field in db._WORKSPACE_MODEL_ROLES:
                provider_id = db.model_provider_id(value)
                if provider_id is not None and provider_id not in self.providers:
                    raise db.OptimizationConflictError("The selected provider no longer exists")
        for k, v in changes.items():
            if v is None:
                self.overrides.pop(k, None)
            else:
                self.overrides[k] = v
        return dict(self.overrides)

    async def reset_overrides(self, _sessionmaker) -> dict[str, object]:
        self.overrides.clear()
        return {}

    # --- LLM Providers ---

    async def list_providers(self, _sessionmaker) -> list[db.LLMProvider]:
        return sorted(self.providers.values(), key=lambda p: (p.name, p.id))

    async def get_provider(self, _sessionmaker, provider_id: UUID) -> db.LLMProvider | None:
        return self.providers.get(provider_id)

    async def get_provider_by_name(self, _sessionmaker, name: str) -> db.LLMProvider | None:
        return next((p for p in self.providers.values() if p.name == name), None)

    async def create_provider(self, _sessionmaker, **kwargs) -> db.LLMProvider:
        if any(p.name == kwargs["name"] for p in self.providers.values()):
            raise db.OptimizationConflictError(
                f"An LLM provider named '{kwargs['name']}' already exists"
            )
        now = datetime.now(UTC)
        provider = db.LLMProvider(
            id=uuid4(),
            name=kwargs["name"],
            dialect=kwargs["dialect"],
            api_mode=kwargs.get("api_mode", "auto"),
            base_url=kwargs.get("base_url"),
            api_key_ciphertext=kwargs.get("api_key_ciphertext"),
            api_key_hint=kwargs.get("api_key_hint"),
            default_model=kwargs.get("default_model"),
            cached_models_json=kwargs.get("cached_models_json"),
            last_tested_at=kwargs.get("last_tested_at"),
            created_at=now,
            updated_at=now,
        )
        self.providers[provider.id] = provider
        return provider

    async def update_provider(
        self, _sessionmaker, provider_id: UUID, **kwargs
    ) -> db.LLMProvider | None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return None
        expected = kwargs.pop("expected_updated_at", None)
        if expected is not None and expected != provider.updated_at:
            raise db.OptimizationConflictError("Provider changed during the request; retry")
        new_name = kwargs.get("name", db._UNSET)
        if new_name is not db._UNSET and any(
            p.id != provider_id and p.name == new_name for p in self.providers.values()
        ):
            raise db.OptimizationConflictError(f"An LLM provider named '{new_name}' already exists")
        for key, value in kwargs.items():
            if value is not db._UNSET:
                setattr(provider, key, value)
        provider.updated_at = datetime.now(UTC)
        return provider

    async def delete_provider(self, _sessionmaker, provider_id: UUID) -> db.LLMProvider | None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return None
        db._guard_workspace_provider_removal(self.overrides, provider_id)
        if any(p.active and p.decision_provider_id == provider_id for p in self.promotions):
            raise db.OptimizationConflictError(
                "LLM provider is currently referenced by an active optimization promotion"
            )
        pid_str = str(provider_id)
        for job in self.jobs.values():
            if job.status in {"queued", "running"} and (
                job.decision_provider_id == provider_id
                or db._config_references_provider(job.config_json, pid_str)
            ):
                raise db.OptimizationConflictError(
                    "LLM provider is currently in use by an active optimization job"
                )
        return self.providers.pop(provider_id)

    # --- Datasets ---

    async def create_dataset(self, _sessionmaker, **kwargs) -> db.OptimizationDataset:
        for existing in self.datasets.values():
            if existing.name == kwargs["name"]:
                raise RuntimeError("duplicate")
        ds = db.OptimizationDataset(
            id=kwargs.get("dataset_id") or uuid4(),
            name=kwargs["name"],
            target=kwargs["target"],
            storage_backend=kwargs.get("storage_backend", "neon"),
            storage_volume=kwargs.get("storage_volume"),
            object_key=kwargs["object_key"],
            sha256=kwargs["sha256"],
            size_bytes=kwargs["size_bytes"],
            row_count=kwargs["row_count"],
            summary_json=kwargs["summary_json"],
            created_at=datetime.now(UTC),
        )
        self.datasets[ds.id] = ds
        return ds

    async def list_datasets(self, _sessionmaker) -> list[db.OptimizationDataset]:
        return sorted(self.datasets.values(), key=lambda d: d.created_at, reverse=True)

    async def get_dataset(self, _sessionmaker, dataset_id: UUID) -> db.OptimizationDataset | None:
        return self.datasets.get(dataset_id)

    async def get_dataset_by_name(self, _sessionmaker, name: str) -> db.OptimizationDataset | None:
        return next((d for d in self.datasets.values() if d.name == name), None)

    async def delete_dataset(
        self, _sessionmaker, dataset_id: UUID
    ) -> db.OptimizationDataset | None:
        ds = self.datasets.get(dataset_id)
        if ds is None:
            return None
        for job in self.jobs.values():
            if job.dataset_id == dataset_id and job.status in {"queued", "running"}:
                raise db.OptimizationConflictError("Dataset is currently in use by an active job")
        ds.summary_json = {**ds.summary_json, "deletion_pending": True}
        return ds

    async def finish_delete_dataset(self, _sessionmaker, dataset_id):
        self.datasets.pop(dataset_id, None)

    # --- Jobs ---

    async def create_job(self, _sessionmaker, **kwargs) -> db.OptimizationJob:
        for existing in self.jobs.values():
            if existing.target == kwargs["target"] and existing.status in {"queued", "running"}:
                raise db.OptimizationConflictError(
                    f"Target '{kwargs['target']}' already has an active optimization job"
                )
        now = datetime.now(UTC)
        job = db.OptimizationJob(
            id=uuid4(),
            target=kwargs["target"],
            program_kind=kwargs["program_kind"],
            status="queued",
            stage="queued",
            dataset_source=kwargs["dataset_source"],
            dataset_name=kwargs["dataset_name"],
            dataset_id=kwargs["dataset_id"],
            decision_provider_id=kwargs.get("decision_provider_id"),
            config_json=kwargs["config_json"],
            progress_json={},
            log_tail="",
            cancel_requested=False,
            mlflow_experiment=kwargs["mlflow_experiment"],
            created_at=now,
            heartbeat_at=now,
        )
        self.jobs[job.id] = job
        return job

    async def touch_heartbeat(self, _sessionmaker, job_id: UUID) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return False
        job.heartbeat_at = datetime.now(UTC)
        self.heartbeat_touches.append(job_id)
        return True

    async def update_progress(
        self, _sessionmaker, job_id: UUID, **kwargs
    ) -> db.OptimizationJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return None
        for k, v in kwargs.items():
            if v is not None:
                setattr(job, k, v)
        job.heartbeat_at = datetime.now(UTC)
        return job

    async def finish_job(self, _sessionmaker, job_id: UUID, **kwargs) -> db.OptimizationJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return None
        for k, v in kwargs.items():
            if v is not None:
                setattr(job, k, v)
        now = datetime.now(UTC)
        job.heartbeat_at = now
        job.finished_at = now
        return job

    async def request_cancel(self, _sessionmaker, job_id: UUID) -> db.OptimizationJob | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.status in {"queued", "running"}:
            job.cancel_requested = True
            if job.status == "queued":
                job.stage = "cancelling"
        return job

    async def is_cancel_requested(self, _sessionmaker, job_id: UUID) -> bool:
        job = self.jobs.get(job_id)
        return bool(job and job.cancel_requested)

    async def reap_stale(self, _sessionmaker, timeout_seconds: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        count = 0
        for job in self.jobs.values():
            ref = job.heartbeat_at or job.started_at or job.created_at
            if job.status in {"queued", "running"} and ref < cutoff:
                job.status = "failed"
                job.stage = "failed"
                job.error = "Optimization job timed out or worker restarted"
                job.finished_at = datetime.now(UTC)
                count += 1
        return count

    async def list_jobs(self, _sessionmaker, *, target=None, status=None, limit=50):
        rows = [
            j
            for j in self.jobs.values()
            if (target is None or j.target == target) and (status is None or j.status == status)
        ]
        rows.sort(key=lambda j: (j.created_at, j.id), reverse=True)
        return rows[:limit]

    async def get_job(self, _sessionmaker, job_id: UUID) -> db.OptimizationJob | None:
        return self.jobs.get(job_id)

    # --- Promotions & Overview ---

    async def promote(self, _sessionmaker, **kwargs) -> db.OptimizationPromotion:
        for p in self.promotions:
            if p.target == kwargs["target"] and p.active:
                p.active = False
                p.rolled_back_at = datetime.now(UTC)
        self._promo_seq += 1
        promo = db.OptimizationPromotion(
            id=uuid4(),
            target=kwargs["target"],
            job_id=kwargs["job_id"],
            artifact_object_key=kwargs["artifact_object_key"],
            artifact_sha256=kwargs["artifact_sha256"],
            artifact_size_bytes=kwargs["artifact_size_bytes"],
            resolved_decision_model=kwargs.get("resolved_decision_model"),
            decision_provider_id=kwargs.get("decision_provider_id"),
            program_kind=kwargs.get("program_kind", "rlm" if kwargs["target"] == "rlm" else None),
            active=True,
            promoted_at=datetime.now(UTC) + timedelta(seconds=self._promo_seq),
        )
        self.promotions.append(promo)
        return promo

    async def rollback(
        self, _sessionmaker, target: str, *, mode: str = "previous"
    ) -> db.OptimizationPromotion | None:
        active = next((p for p in self.promotions if p.target == target and p.active), None)
        if active is None:
            return None
        active.active = False
        active.rolled_back_at = datetime.now(UTC)
        if mode != "previous":
            return None
        older = [
            p
            for p in self.promotions
            if p.target == target
            and not p.active
            and (p.promoted_at, p.id) < (active.promoted_at, active.id)
        ]
        if not older:
            return None
        older.sort(key=lambda p: (p.promoted_at, p.id), reverse=True)
        prev = older[0]
        prev.active = True
        prev.rolled_back_at = None
        return prev

    async def list_active_promotions(self, _sessionmaker) -> dict[str, db.OptimizationPromotion]:
        return {p.target: p for p in self.promotions if p.active}

    async def list_promotion_history(self, _sessionmaker, target: str, limit: int = 10):
        rows = [p for p in self.promotions if p.target == target]
        rows.sort(key=lambda p: (p.promoted_at, p.id), reverse=True)
        return rows[:limit]

    async def load_overview(self, _sessionmaker) -> opt_runner.OverviewData:
        targets: dict[opt_datasets.OptimizationTarget, opt_runner.OverviewTargetData] = {}
        active_map = {p.target: p for p in self.promotions if p.active}
        for target in opt_datasets.VALID_OPT_TARGETS:
            active_promo = active_map.get(target)
            prev_promo: db.OptimizationPromotion | None = None
            if active_promo is not None:
                older = [
                    p
                    for p in self.promotions
                    if p.target == target
                    and not p.active
                    and (p.promoted_at, p.id) < (active_promo.promoted_at, active_promo.id)
                ]
                older.sort(key=lambda p: (p.promoted_at, p.id), reverse=True)
                prev_promo = older[0] if older else None
            t_jobs = sorted(
                (j for j in self.jobs.values() if j.target == target),
                key=lambda j: (j.created_at, j.id),
                reverse=True,
            )
            active_job = next((j for j in t_jobs if j.status in {"queued", "running"}), None)
            latest_job = t_jobs[0] if t_jobs else None
            targets[target] = opt_runner.OverviewTargetData(
                target=target,
                active_promotion=active_promo,
                previous_promotion=prev_promo,
                latest_job=latest_job,
                latest_scored_job=next(
                    (
                        j
                        for j in t_jobs
                        if j.status == "completed" and j.optimized_score is not None
                    ),
                    None,
                ),
                active_job=active_job,
            )
        provider_names = {p.id: p.name for p in self.providers.values()}
        return opt_runner.OverviewData(
            workspace_overrides=dict(self.overrides),
            targets=targets,
            jobs_by_id=dict(self.jobs),
            provider_names=provider_names,
        )


class FakeStorage:
    optimization_artifact_key = staticmethod(ObjectStorage.optimization_artifact_key)
    optimization_dataset_key = staticmethod(ObjectStorage.optimization_dataset_key)

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: int = 0
        self.download_calls: int = 0
        self.delete_calls: int = 0

    def close(self) -> None:
        pass

    async def put_object(self, key: str, path: Path) -> tuple[str, int]:
        self.put_calls += 1
        raw = path.read_bytes()
        self.objects[key] = raw
        return hashlib.sha256(raw).hexdigest(), len(raw)

    async def download(self, key: str, path: Path, *, sha256: str, size: int) -> None:
        self.download_calls += 1
        raw = self.objects[key]
        assert hashlib.sha256(raw).hexdigest() == sha256
        assert len(raw) == size
        path.write_bytes(raw)

    async def delete_object(self, key: str) -> None:
        self.delete_calls += 1
        self.objects.pop(key, None)


class FakeDaytonaVolume:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.write_calls: int = 0
        self.read_calls: int = 0
        self.delete_calls: int = 0

    async def write(self, settings: Settings, rel_path: str, content: bytes, *, daytona=None):
        self.write_calls += 1
        self.files[rel_path] = bytes(content)
        return settings.daytona_dataset_volume, rel_path

    async def read(self, settings: Settings, rel_path: str, *, daytona=None) -> bytes:
        self.read_calls += 1
        if rel_path not in self.files:
            raise interpreter.DatasetVolumeObjectMissing(f"Missing {rel_path}")
        return self.files[rel_path]

    async def delete(self, settings: Settings, rel_path: str, *, daytona=None) -> None:
        self.delete_calls += 1
        self.files.pop(rel_path, None)


def _fake_dns_public(host: str, port: int, **_kwargs):
    """Deterministic DNS stub returning public IPs for test hostnames without network."""
    lower = host.lower().rstrip(".")
    if lower in {"127.0.0.1", "localhost"}:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    if lower == "169.254.169.254":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
    if lower.startswith("10.") or lower.startswith("192.168."):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (lower, port))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest_asyncio.fixture
async def opt_app(monkeypatch, tmp_path):
    eval_dir = tmp_path / "eval_datasets"
    eval_dir.mkdir()
    (eval_dir / "skills.jsonl").write_text(
        json.dumps(
            {
                "task": "Analyze the CSV file",
                "needs_skill": True,
                "expected_skill": "table",
                "split": "train",
                "lineage": "csv-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (eval_dir / "memory.jsonl").write_text(
        json.dumps(
            {
                "task": "Remember currency EUR",
                "answer": "Noted",
                "worth_saving": True,
                "action": "insert",
                "scope": "global",
                "split": "train",
                "lineage": "cur-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (eval_dir / "rlm.jsonl").write_text(
        json.dumps(
            {
                "task": "Count rows",
                "answer": "42",
                "verifier": {"type": "exact", "expected": "42"},
                "split": "train",
                "lineage": "cnt-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    skills_dir = tmp_path / "skills"
    (skills_dir / "table").mkdir(parents=True)
    (skills_dir / "table" / "SKILL.md").write_text(
        "---\nname: table\ndescription: Analyze CSV tables\n---\nUse pandas.\n",
        encoding="utf-8",
    )

    cache_dir = tmp_path / "artifact_cache"
    settings = Settings(
        _env_file=None,
        database_url="postgresql://primaryuser:primarysecret@primary-host/neondb",
        database_direct_url="postgresql://primaryuser:primarysecret@primary-host/neondb",
        optimization_database_url="postgresql://optuser:optsecret@opt-branch-host/neondb",
        daytona_api_key="dtn_host_secret_key_1234",
        daytona_image="offline",
        s3_access_key_id="s3-key-secret",
        s3_secret_access_key="s3-secret-secret",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="sk-workspace-secret-1234567890",
        credentials_encryption_key=_FERNET_KEY,
        mlflow_tracking_uri="http://127.0.0.1:5001",
        api_bearer_token="opt-token",
        provider_admin_token="opt-token",
        skills_dir=skills_dir,
        eval_datasets_dir=eval_dir,
        artifact_cache_dir=cache_dir,
    )

    mem_store = MemoryOptStore()
    storage = FakeStorage()
    volume = FakeDaytonaVolume()
    opt_runner._BUILTIN_SUMMARY_CACHE.clear()

    @asynccontextmanager
    async def get_db_session():
        class MiniDb:
            async def get(self, model, identity):
                if model is db.OptimizationJob:
                    return mem_store.jobs.get(identity)
                return None

        yield MiniDb()

    session_factory = lambda: get_db_session()  # noqa: E731

    @asynccontextmanager
    async def daytona_ctx(_settings):
        yield SimpleNamespace(v2=SimpleNamespace())

    monkeypatch.setattr(llm_registry.socket, "getaddrinfo", _fake_dns_public)
    monkeypatch.setattr(api, "Settings", lambda: settings)
    monkeypatch.setattr(api, "ObjectStorage", lambda _s: storage)
    monkeypatch.setattr(api, "make_daytona_client", daytona_ctx)
    monkeypatch.setattr(interpreter, "write_volume_dataset", volume.write)
    monkeypatch.setattr(interpreter, "read_volume_dataset", volume.read)
    monkeypatch.setattr(interpreter, "delete_volume_dataset", volume.delete)
    monkeypatch.setattr(interpreter, "close_dataset_volume_helpers", AsyncMock())
    monkeypatch.setattr(opt_runner, "load_overview_data", mem_store.load_overview)
    monkeypatch.setattr(
        api.store, "create_db", lambda _s: (SimpleNamespace(dispose=AsyncMock()), session_factory)
    )
    monkeypatch.setattr(api.store, "reap_stale_runs", AsyncMock(return_value=0))
    monkeypatch.setattr(api.store, "get_workspace_settings_overrides", mem_store.get_overrides)
    monkeypatch.setattr(
        api.store, "update_workspace_settings_overrides", mem_store.update_overrides
    )
    monkeypatch.setattr(api.store, "reset_workspace_settings_overrides", mem_store.reset_overrides)
    monkeypatch.setattr(api.store, "list_llm_providers", mem_store.list_providers)
    monkeypatch.setattr(api.store, "get_llm_provider", mem_store.get_provider)
    monkeypatch.setattr(api.store, "get_llm_provider_by_name", mem_store.get_provider_by_name)
    monkeypatch.setattr(api.store, "create_llm_provider", mem_store.create_provider)
    monkeypatch.setattr(api.store, "update_llm_provider", mem_store.update_provider)
    monkeypatch.setattr(api.store, "delete_llm_provider", mem_store.delete_provider)
    monkeypatch.setattr(api.store, "create_optimization_dataset", mem_store.create_dataset)
    monkeypatch.setattr(api.store, "list_optimization_datasets", mem_store.list_datasets)
    monkeypatch.setattr(api.store, "get_optimization_dataset", mem_store.get_dataset)
    monkeypatch.setattr(
        api.store, "get_optimization_dataset_by_name", mem_store.get_dataset_by_name
    )
    monkeypatch.setattr(
        api.store, "finish_optimization_dataset_deletion", mem_store.finish_delete_dataset
    )
    monkeypatch.setattr(api.store, "begin_optimization_dataset_deletion", mem_store.delete_dataset)
    monkeypatch.setattr(api.store, "create_optimization_job", mem_store.create_job)
    monkeypatch.setattr(api.store, "touch_optimization_job_heartbeat", mem_store.touch_heartbeat)
    monkeypatch.setattr(api.store, "update_optimization_job_progress", mem_store.update_progress)
    monkeypatch.setattr(api.store, "finish_optimization_job", mem_store.finish_job)
    monkeypatch.setattr(api.store, "request_optimization_job_cancel", mem_store.request_cancel)
    monkeypatch.setattr(
        api.store, "is_optimization_job_cancel_requested", mem_store.is_cancel_requested
    )
    monkeypatch.setattr(api.store, "reap_stale_optimization_jobs", mem_store.reap_stale)
    monkeypatch.setattr(api.store, "list_optimization_jobs", mem_store.list_jobs)
    monkeypatch.setattr(api.store, "get_optimization_job", mem_store.get_job)
    monkeypatch.setattr(api.store, "promote_optimization_artifact", mem_store.promote)
    monkeypatch.setattr(api.store, "rollback_optimization_target", mem_store.rollback)
    monkeypatch.setattr(
        api.store, "list_active_optimization_promotions", mem_store.list_active_promotions
    )
    monkeypatch.setattr(
        api.store, "list_target_promotion_history", mem_store.list_promotion_history
    )
    monkeypatch.setattr(api.mlflow, "set_tracking_uri", lambda _u: None)
    monkeypatch.setattr("fleet_rlm.telemetry._experiment_ids", lambda *_: ("7", "7"))
    monkeypatch.setattr(api.mlflow.tracing, "set_destination", lambda *_: None)
    monkeypatch.setattr(api.mlflow.dspy, "autolog", lambda *_a, **_k: None)

    async with api.lifespan(api.app):
        async with asyncio.timeout(2):
            while api.app.state.runtime.tracking_pending:
                await asyncio.sleep(0.001)
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            yield SimpleNamespace(
                client=client,
                store=mem_store,
                storage=storage,
                volume=volume,
                settings=settings,
            )


def _swap_runtime_settings(new_settings: Settings, **extra: object) -> api.Runtime:
    current: api.Runtime = api.app.state.runtime
    replacement = api.Runtime(
        settings=new_settings,
        sessionmaker=current.sessionmaker,
        daytona=current.daytona,
        volume_daytona=current.volume_daytona,
        storage=current.storage,
        skills=current.skills,
        lm=current.lm,
        sub_lm=current.sub_lm,
        decision_lm=current.decision_lm,
        turn_semaphore=current.turn_semaphore,
        optimized_program_state=current.optimized_program_state,
        mlflow_experiment_id=extra.get("mlflow_experiment_id", current.mlflow_experiment_id),  # type: ignore[arg-type]
        artifact_cache_dir=current.artifact_cache_dir,
        rlm_state_cache=current.rlm_state_cache,
        optimization_tasks=current.optimization_tasks,
        optimization_procs=current.optimization_procs,
        optimization_cancel_events=current.optimization_cancel_events,
        shutdown_event=current.shutdown_event,
    )
    api.app.state.runtime = replacement
    return replacement


def test_alembic_0008_migration_structure() -> None:
    mod = importlib.import_module("fleet_rlm")
    alembic_path = (
        Path(mod.__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0008_optimization_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("m0008", alembic_path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "0008_optimization_jobs"
    assert m.down_revision == "0007_turn_lessons"


@pytest.mark.asyncio
async def test_datasets_neon_and_daytona_volume_dispatch_caching_and_guards(
    opt_app, monkeypatch
) -> None:
    client = opt_app.client
    storage = opt_app.storage
    volume = opt_app.volume

    # Built-in discovery includes storage_backend='builtin' and caches by mtime+size
    listed = (await client.get("/api/optimizations/datasets", headers=_AUTH)).json()
    assert [item["id"] for item in listed] == ["builtin:skills", "builtin:memory", "builtin:rlm"]
    assert all(item["storage_backend"] == "builtin" for item in listed)
    assert all(item["storage_volume"] is None for item in listed)

    with monkeypatch.context() as cache_patch:
        cache_patch.setattr(
            opt_datasets,
            "validate_and_summarize_jsonl",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should hit mtime+size cache")),
        )
        cached_list = (await client.get("/api/optimizations/datasets", headers=_AUTH)).json()
        assert [item["id"] for item in cached_list] == [
            "builtin:skills",
            "builtin:memory",
            "builtin:rlm",
        ]

    preview = await client.get(
        "/api/optimizations/datasets/builtin:skills/preview?limit=1", headers=_AUTH
    )
    assert preview.status_code == 200
    assert preview.json()["dataset"]["storage_backend"] == "builtin"
    assert len(preview.json()["rows"]) == 1

    # Secret pattern rejection
    bad_secret = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "skills"},
        files={
            "file": (
                "leak.jsonl",
                b'{"task": "sk-secretsecretsecret12345", "needs_skill": false}\n'
                b'{"task": "hello", "needs_skill": false}\n',
                "text/plain",
            )
        },
    )
    assert bad_secret.status_code == 422

    # Control characters in name rejected
    bad_name = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "memory", "name": "bad\x00name"},
        files={"file": ("mem.jsonl", _valid_memory_jsonl(), "application/x-ndjson")},
    )
    assert bad_name.status_code == 422

    # Unknown skill rejection
    bad_skill = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "skills"},
        files={
            "file": (
                "unknown.jsonl",
                b'{"task": "Run", "needs_skill": true, "expected_skill": "missing"}\n'
                b'{"task": "Hi", "needs_skill": false}\n',
                "text/plain",
            )
        },
    )
    assert bad_skill.status_code == 422

    # Valid Neon S3 upload + duplicate-before-write check + SHA cache hit + delete
    neon_resp = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "memory", "name": "custom-mem.jsonl", "storage_backend": "neon"},
        files={"file": ("custom-mem.jsonl", _valid_memory_jsonl(), "application/x-ndjson")},
    )
    assert neon_resp.status_code == 201, neon_resp.text
    neon_body = neon_resp.json()
    assert neon_body["storage_backend"] == "neon"
    assert neon_body["storage_volume"] is None
    assert storage.put_calls == 1
    assert len(storage.objects) == 1

    # Duplicate name fails with 409 BEFORE touching S3 or Daytona Volume!
    dup_resp = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "memory", "name": "custom-mem.jsonl", "storage_backend": "neon"},
        files={"file": ("custom-mem.jsonl", _valid_memory_jsonl(), "application/x-ndjson")},
    )
    assert dup_resp.status_code == 409
    assert storage.put_calls == 1

    # Preview hits host SHA-256 cache warmed on upload (0 S3 downloads)
    ds_preview = await client.get(
        f"/api/optimizations/datasets/{neon_body['id']}/preview", headers=_AUTH
    )
    assert ds_preview.status_code == 200
    assert ds_preview.json()["rows"][0]["task"] == "Remember timezone UTC"
    assert storage.download_calls == 0

    # Delete custom Neon dataset cleans S3
    deleted = await client.delete(f"/api/optimizations/datasets/{neon_body['id']}", headers=_AUTH)
    assert deleted.status_code == 204
    assert len(storage.objects) == 0

    # Daytona Volume upload + cache-miss read + cache-hit read + volume delete
    vol_resp = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={
            "target": "memory",
            "name": "vol-mem.jsonl",
            "storage_backend": "daytona_volume",
        },
        files={"file": ("vol-mem.jsonl", _valid_memory_jsonl(), "application/x-ndjson")},
    )
    assert vol_resp.status_code == 201, vol_resp.text
    vol_body = vol_resp.json()
    assert vol_body["storage_backend"] == "daytona_volume"
    assert vol_body["storage_volume"] == "fleet-eval-datasets"
    assert volume.write_calls == 1
    assert len(volume.files) == 1
    assert len(storage.objects) == 0

    # Clear host SHA cache to force a Daytona Volume read on preview
    cached_file = opt_app.settings.artifact_cache_dir / f"{vol_body['sha256']}.jsonl"
    cached_file.unlink()
    vol_preview1 = await client.get(
        f"/api/optimizations/datasets/{vol_body['id']}/preview", headers=_AUTH
    )
    assert vol_preview1.status_code == 200
    assert vol_preview1.json()["dataset"]["storage_backend"] == "daytona_volume"
    assert volume.read_calls == 1
    assert cached_file.is_file()
    assert (opt_app.settings.artifact_cache_dir.stat().st_mode & 0o777) == 0o700

    # Second preview hits host SHA cache (still 1 volume read)
    vol_preview2 = await client.get(
        f"/api/optimizations/datasets/{vol_body['id']}/preview", headers=_AUTH
    )
    assert vol_preview2.status_code == 200
    assert volume.read_calls == 1

    async def unavailable_delete(*args, **kwargs):
        raise interpreter.DatasetVolumeError("Volume unavailable")

    with monkeypatch.context() as offline:
        offline.setattr(interpreter, "delete_volume_dataset", unavailable_delete)
        failed = await client.delete(f"/api/optimizations/datasets/{vol_body['id']}", headers=_AUTH)
        assert failed.status_code == 503
        assert opt_app.store.datasets[UUID(vol_body["id"])].summary_json["deletion_pending"]
        preview = await client.get(
            f"/api/optimizations/datasets/{vol_body['id']}/preview", headers=_AUTH
        )
        assert preview.status_code == 409

    # Retrying the same delete cleans the remote object and its index.

    vol_del = await client.delete(f"/api/optimizations/datasets/{vol_body['id']}", headers=_AUTH)
    assert vol_del.status_code == 204
    assert not cached_file.exists()
    assert volume.delete_calls == 1
    assert len(volume.files) == 0

    # Cannot delete builtin
    del_builtin = await client.delete("/api/optimizations/datasets/builtin:skills", headers=_AUTH)
    assert del_builtin.status_code == 400


@pytest.mark.asyncio
async def test_registered_typesafe_workspace_decision_model(opt_app):
    client = opt_app.client
    model = "jev-preview"
    created = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Registered TypeSafe",
            "dialect": "typesafe",
            "api_key": "test-typesafe-key",
            "default_model": model,
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    qualified = f"provider/{provider_id}/{model}"
    invalid = await client.patch(
        "/api/settings", headers=_AUTH, json={"decision_model": f"provider/{provider_id}/other"}
    )
    assert invalid.status_code == 422
    saved = await client.patch("/api/settings", headers=_AUTH, json={"decision_model": qualified})
    assert saved.status_code == 200
    loaded = await client.get("/api/settings", headers=_AUTH)
    assert loaded.json()["effective"]["decision_model"] == qualified

    runtime = api.app.state.runtime
    effective = await api._effective_settings(runtime)
    lm = await api._request_decision_lm(runtime, effective)
    assert lm.model == "typesafe:jev-preview"
    assert lm.kwargs["api_key"] == "test-typesafe-key"
    roles, decision_provider_id = await api._resolve_role_credentials(
        runtime, effective, api.OptimizationJobCreate(target="skills")
    )
    assert decision_provider_id == UUID(provider_id)
    assert roles["decision_lm"].model == model
    assert roles["decision_lm"].api_key == "test-typesafe-key"

    route = f"/api/optimizations/providers/{provider_id}"
    blocked = await client.delete(route, headers=_AUTH)
    assert blocked.status_code == 409
    assert "Decision Model" in blocked.json()["detail"]
    reassigned = await client.patch("/api/settings", headers=_AUTH, json={"decision_model": None})
    assert reassigned.status_code == 200
    assert (await client.delete(route, headers=_AUTH)).status_code == 204
    deleted_assignment = await client.patch(
        "/api/settings", headers=_AUTH, json={"llm_model": qualified}
    )
    assert deleted_assignment.status_code == 409


@pytest.mark.asyncio
async def test_provider_crud_encryption_ssrf_probe_models_and_delete_guard(
    opt_app, monkeypatch
) -> None:
    client = opt_app.client
    mem_store = opt_app.store

    shared_settings = opt_app.settings.model_copy(
        update={"provider_admin_token": SecretStr("separate-admin")}
    )
    _swap_runtime_settings(shared_settings)
    denied = await client.post(
        "/api/optimizations/providers", headers=_AUTH, json={"name": "Denied", "dialect": "openai"}
    )
    assert denied.status_code == 403
    _swap_runtime_settings(opt_app.settings)

    # 1. Overview capabilities reflect encryption & storage flags
    ov = (await client.get("/api/optimizations/overview", headers=_AUTH)).json()
    caps = ov["capabilities"]
    assert caps["neon_storage_enabled"] is True
    assert caps["daytona_volume_enabled"] is True
    assert caps["daytona_dataset_volume"] == "fleet-eval-datasets"
    assert caps["credentials_encryption_enabled"] is True
    assert caps["allow_private_llm_endpoints"] is False
    assert caps["allow_local_interpreter"] is False

    # 2. SSRF URL blocked on create (metadata + private IP when allow_private=False)
    ssrf_resp = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Bad IMDS",
            "dialect": "openai_compatible",
            "base_url": "http://169.254.169.254/latest",
        },
    )
    assert ssrf_resp.status_code == 422

    private_resp = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Local Ollama Blocked",
            "dialect": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    )
    assert private_resp.status_code == 422

    # 3. Without FLEET_CREDENTIALS_ENCRYPTION_KEY: API-keyed fails with 400; keyless succeeds!
    no_enc_settings = opt_app.settings.model_copy(
        update={"credentials_encryption_key": None, "allow_private_llm_endpoints": True}
    )
    _swap_runtime_settings(no_enc_settings)

    keyed_no_enc = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "OpenAI No Key",
            "dialect": "openai",
            "api_key": "sk-proj-secretvalue9a4f",
        },
    )
    assert keyed_no_enc.status_code == 400
    assert "FLEET_CREDENTIALS_ENCRYPTION_KEY" in keyed_no_enc.json()["detail"]

    keyless_local = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Local Ollama",
            "dialect": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    )
    assert keyless_local.status_code == 201, keyless_local.text
    assert keyless_local.json()["has_api_key"] is False
    assert keyless_local.json()["api_key_hint"] is None

    # Restore encryption-enabled settings
    _swap_runtime_settings(opt_app.settings)

    # 4. Create API-keyed provider -> encrypted at rest, masked hint returned, never plaintext!
    created_resp = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Primary Gateway",
            "dialect": "openai_compatible",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "sk-proj-supersecretkey9a4f",
            "default_model": "gpt-4.1",
        },
    )
    assert created_resp.status_code == 201, created_resp.text
    created = created_resp.json()
    provider_id = created["id"]
    assert created["has_api_key"] is True
    assert created["api_key_hint"] == "sk-proj-...9a4f"
    assert created["api_mode"] == "chat"
    assert "supersecretkey" not in json.dumps(created)
    stored_row = mem_store.providers[UUID(provider_id)]
    assert stored_row.api_key_ciphertext is not None
    assert "supersecretkey" not in stored_row.api_key_ciphertext
    assert (
        llm_registry.decrypt_secret(stored_row.api_key_ciphertext, opt_app.settings)
        == "sk-proj-supersecretkey9a4f"
    )

    qualified = f"provider/{provider_id}/gpt-4.1"
    lm, label = await api._chat_model(api.app.state.runtime, opt_app.settings, qualified)
    assert label == "Primary Gateway · gpt-4.1"
    assert lm.kwargs["api_base"] == "https://gateway.example.com/v1"
    assert lm.kwargs["api_key"] == "sk-proj-supersecretkey9a4f"
    defaults = opt_app.settings.model_copy(
        update={"llm_model": qualified, "sub_llm_model": qualified}
    )
    primary, inherited_label = await api._chat_model(api.app.state.runtime, defaults, None)
    sub = await api._workspace_sub_lm(api.app.state.runtime, defaults)
    assert inherited_label == label
    assert primary.kwargs["api_base"] == sub.kwargs["api_base"] == lm.kwargs["api_base"]

    # List returns encryption_configured & allow_private_endpoints
    listed = (await client.get("/api/optimizations/providers", headers=_AUTH)).json()
    assert listed["encryption_configured"] is True
    assert listed["allow_private_endpoints"] is False
    assert len(listed["providers"]) == 2

    # 5. Probe with stored provider_id uses decrypted key when api_key is omitted
    observed_probe: dict[str, object] = {}

    async def fake_fetch(dialect, base_url, api_key=None, *, allow_private=False, timeout=8.0):
        observed_probe.update({"dialect": dialect, "base_url": base_url, "api_key": api_key})
        return [
            {"id": "gpt-4.1", "label": "GPT-4.1", "owned_by": "openai"},
            {"id": "gpt-4.1-mini", "label": "GPT-4.1 Mini"},
        ]

    monkeypatch.setattr(llm_registry, "fetch_provider_models", fake_fetch)
    changed_endpoint = {"base_url": "https://new.example.com/v1"}
    assert (
        await client.patch(
            f"/api/optimizations/providers/{provider_id}", headers=_AUTH, json=changed_endpoint
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/optimizations/providers/probe",
            headers=_AUTH,
            json={"provider_id": provider_id, **changed_endpoint},
        )
    ).status_code == 422
    assert not observed_probe
    probe_resp = await client.post(
        "/api/optimizations/providers/probe",
        headers=_AUTH,
        json={"provider_id": provider_id},
    )
    assert probe_resp.status_code == 200
    assert probe_resp.json()["ok"] is True
    assert probe_resp.json()["count"] == 2
    assert observed_probe["api_key"] == "sk-proj-supersecretkey9a4f"

    # 6. Refresh /models caches models & updates last_tested_at
    refresh_resp = await client.post(
        f"/api/optimizations/providers/{provider_id}/models", headers=_AUTH
    )
    assert refresh_resp.status_code == 200
    refreshed = refresh_resp.json()
    assert refreshed["count"] == 2
    assert len(refreshed["provider"]["cached_models"]) == 2
    assert refreshed["provider"]["last_tested_at"] is not None

    async def rotate_during_fetch(*args, **kwargs):
        await mem_store.update_provider(None, UUID(provider_id), default_model="updated")
        return []

    with monkeypatch.context() as racing:
        racing.setattr(llm_registry, "fetch_provider_models", rotate_during_fetch)
        stale = await client.post(
            f"/api/optimizations/providers/{provider_id}/models", headers=_AUTH
        )
        assert stale.status_code == 409

    # 7. PATCH updates default_model & clear_api_key
    patch_resp = await client.patch(
        f"/api/optimizations/providers/{provider_id}",
        headers=_AUTH,
        json={"default_model": "gpt-4.1-mini", "api_mode": "responses"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["default_model"] == "gpt-4.1-mini"
    assert patch_resp.json()["api_mode"] == "responses"
    assert patch_resp.json()["has_api_key"] is True
    rotated = await client.patch(
        f"/api/optimizations/providers/{provider_id}", headers=_AUTH, json={"clear_api_key": True}
    )
    assert rotated.json()["cached_models"] == []
    assert rotated.json()["last_tested_at"] is None

    # 8. Delete guard: 409 when referenced by active job or active promotion
    active_job_id = uuid4()
    mem_store.jobs[active_job_id] = db.OptimizationJob(
        id=active_job_id,
        target="rlm",
        status="running",
        stage="gepa",
        dataset_source="builtin",
        dataset_name="rlm.jsonl",
        config_json={"models": {"root_lm": {"provider_id": provider_id, "model": "gpt-4.1"}}},
        created_at=datetime.now(UTC),
    )
    blocked_job_del = await client.delete(
        f"/api/optimizations/providers/{provider_id}", headers=_AUTH
    )
    assert blocked_job_del.status_code == 409
    mem_store.jobs[active_job_id].status = "completed"

    mem_store.promotions.append(
        db.OptimizationPromotion(
            id=uuid4(),
            target="skills",
            job_id=active_job_id,
            artifact_object_key="k",
            artifact_sha256="a" * 64,
            artifact_size_bytes=10,
            decision_provider_id=UUID(provider_id),
            active=True,
            promoted_at=datetime.now(UTC),
        )
    )
    blocked_promo_del = await client.delete(
        f"/api/optimizations/providers/{provider_id}", headers=_AUTH
    )
    assert blocked_promo_del.status_code == 409

    mem_store.promotions.clear()
    deleted_resp = await client.delete(f"/api/optimizations/providers/{provider_id}", headers=_AUTH)
    assert deleted_resp.status_code == 204


@pytest.mark.asyncio
async def test_launch_security_guards_local_interpreter_primary_db_port_and_ssrf(opt_app) -> None:
    client = opt_app.client

    # local_interpreter=True rejected with 403 by default
    local_blocked = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={
            "target": "rlm",
            "branch_mode": "configured_branch",
            "local_interpreter": True,
        },
    )
    assert local_blocked.status_code == 403

    # Custom URL matching primary with explicit :5432 and trailing DNS dot is blocked!
    for primary_variant in (
        "postgresql://primary-host/neondb",
        "postgresql://primary-host:5432/neondb",
        "postgresql://PRIMARY-HOST.:5432/neondb/",
    ):
        resp = await client.post(
            "/api/optimizations/jobs",
            headers=_AUTH,
            json={
                "target": "skills",
                "branch_mode": "custom_branch",
                "custom_database_url": primary_variant,
            },
        )
        assert resp.status_code == 422, primary_variant

    # Custom DB URL SSRF (cloud metadata / loopback when allow_private=False) rejected with 422
    for ssrf_db in (
        "postgresql://user:pass@169.254.169.254/neondb",
        "postgresql://user:pass@127.0.0.1:5432/neondb",
    ):
        resp = await client.post(
            "/api/optimizations/jobs",
            headers=_AUTH,
            json={
                "target": "skills",
                "branch_mode": "custom_branch",
                "custom_database_url": ssrf_db,
            },
        )
        assert resp.status_code == 422, ssrf_db

    # Direct mode without opt-in fails
    resp2 = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={"target": "skills", "branch_mode": "direct_readonly", "allow_direct_opt_in": False},
    )
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_credentials_file_allowlisted_env_scrubbing_and_stale_reap_before_409(
    opt_app, monkeypatch, tmp_path
) -> None:
    client = opt_app.client
    mem_store = opt_app.store

    # Put sensitive host vars in os.environ to prove subprocess env allowlist NEVER leaks them!
    monkeypatch.setenv("FLEET_API_BEARER_TOKEN", "host-bearer-super-secret")
    monkeypatch.setenv("FLEET_NEON_API_KEY", "napi_host_neon_super_secret")
    monkeypatch.setenv("FLEET_S3_ACCESS_KEY_ID", "host-s3-key-secret")
    monkeypatch.setenv("FLEET_S3_SECRET_ACCESS_KEY", "host-s3-secret-value")
    monkeypatch.setenv("FLEET_CREDENTIALS_ENCRYPTION_KEY", _FERNET_KEY)
    monkeypatch.setenv("DAYTONA_API_URL", "https://legacy.daytona.invalid")
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://legacy-v2.daytona.invalid")
    monkeypatch.setenv("DAYTONA_API_KEY", "dtn_ambient_secret_key")

    # Configure workspace tuning overrides to verify propagation into subprocess env
    await client.patch(
        "/api/settings",
        headers=_AUTH,
        json={"rlm_max_iters": 11, "history_max_turns": 4, "daytona_exec_timeout_seconds": 77},
    )

    # Create a custom LLM provider for reflection_lm
    prov_resp = await client.post(
        "/api/optimizations/providers",
        headers=_AUTH,
        json={
            "name": "Reflection OpenAI",
            "dialect": "openai_compatible",
            "base_url": "https://reflect.example.com/v1",
            "api_key": "sk-proj-reflectkey9876543210",
            "default_model": "gpt-4.1",
        },
    )
    assert prov_resp.status_code == 201
    prov_id = prov_resp.json()["id"]
    workspace_models = await client.patch(
        "/api/settings",
        headers=_AUTH,
        json={
            "llm_model": f"provider/{prov_id}/gpt-4.1-mini",
            "sub_llm_model": f"provider/{prov_id}/gpt-4.1-nano",
        },
    )
    assert workspace_models.status_code == 200

    # Seed a STALE active RLM job (heartbeat 20m ago) -> must be reaped before 409!
    stale_id = uuid4()
    old_ts = datetime.now(UTC) - timedelta(minutes=20)
    mem_store.jobs[stale_id] = db.OptimizationJob(
        id=stale_id,
        target="rlm",
        status="running",
        stage="gepa",
        dataset_source="builtin",
        dataset_name="rlm.jsonl",
        created_at=old_ts,
        started_at=old_ts,
        heartbeat_at=old_ts,
    )

    captured: dict[str, object] = {}

    async def fake_exec(*cmd, **kwargs):
        cmd_list = list(cmd)
        assert "--database-url" not in cmd_list
        db_file = Path(cmd_list[cmd_list.index("--database-url-file") + 1])
        cred_file = Path(cmd_list[cmd_list.index("--credentials-file") + 1])
        out_path = Path(cmd_list[cmd_list.index("--output") + 1])
        prog_path = Path(cmd_list[cmd_list.index("--progress-file") + 1])
        rep_path = Path(cmd_list[cmd_list.index("--report-file") + 1])

        captured["db_file"] = db_file
        captured["cred_file"] = cred_file
        captured["db_file_mode"] = db_file.stat().st_mode & 0o777
        captured["cred_file_mode"] = cred_file.stat().st_mode & 0o777
        captured["db_file_text"] = db_file.read_text(encoding="utf-8")
        captured["cred_file_json"] = json.loads(cred_file.read_text(encoding="utf-8"))
        captured["env"] = dict(kwargs["env"])

        prog_path.write_text(
            json.dumps(
                {
                    "stage": "gepa",
                    "label": "Running with sk-proj-reflectkey9876543210",
                    "completed": 1,
                    "total": 4,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out_path.write_text('{"stub": true}', encoding="utf-8")
        rep_path.write_text(
            json.dumps(
                {
                    "target": "rlm",
                    "saved": True,
                    "baseline_score": 60.0,
                    "optimized_score": 82.5,
                    "train_size": 10,
                    "val_size": 5,
                    "holdout_size": 5,
                    "mlflow_run_id": "run-rlm-1",
                    "report": {
                        "eligible": True,
                        "saved": True,
                        "debug_note": "leaked sk-proj-reflectkey9876543210 and primarysecret",
                    },
                }
            ),
            encoding="utf-8",
        )

        class FakeProc:
            returncode = None

            def __init__(self):
                self.stdout = asyncio.StreamReader(limit=1024)
                self.stdout.feed_data(b"x" * 5000 + b"sk-proj-reflectkey9876543210\n")
                self.stdout.feed_data(
                    b"connecting postgresql://optuser:optsecret@opt-branch-host/neondb\n"
                )
                self.stdout.feed_data(b"provider key sk-proj-reflectkey9876543210\n")
                self.stdout.feed_eof()

            async def wait(self):
                await asyncio.sleep(0.6)
                self.returncode = 0
                return 0

            def terminate(self):
                self.returncode = 130

            def kill(self):
                self.returncode = -9

        return FakeProc()

    monkeypatch.setattr(opt_runner.asyncio, "create_subprocess_exec", fake_exec)

    started = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={
            "target": "rlm",
            "dataset_id": "builtin:rlm",
            "branch_mode": "configured_branch",
            "reflection_lm_config": {"provider_id": prov_id, "model": "gpt-4.1"},
        },
    )
    assert started.status_code == 201, started.text
    job_id = started.json()["id"]

    # Stale job was reaped automatically!
    assert mem_store.jobs[stale_id].status == "failed"

    # Non-secret provenance in config_json (no api_key!)
    cfg = started.json()["config_json"]
    expected_roles = {
        "root_lm": "gpt-4.1-mini",
        "sub_lm": "gpt-4.1-nano",
        "reflection_lm": "gpt-4.1",
        "judge_lm": "gpt-4.1-mini",
    }
    assert {role: entry["model"] for role, entry in cfg["models"].items()} == expected_roles
    assert all(entry["provider_id"] == prov_id for entry in cfg["models"].values())
    assert "api_key" not in json.dumps(cfg)
    assert "reflectkey" not in json.dumps(cfg)

    # Consume SSE until completion
    sse = await client.get(f"/api/optimizations/jobs/{job_id}/events", headers=_AUTH)
    assert sse.status_code == 200
    events = [
        line.removeprefix("event: ") for line in sse.text.splitlines() if line.startswith("event: ")
    ]
    assert events[0] == "snapshot" and events[-1] == "done"
    assert set(events[1:-1]) <= {"update"}
    assert "[DONE]" not in sse.text

    # Verify 0600 private files & deletion in finally:
    assert captured["db_file_mode"] == 0o600
    assert captured["cred_file_mode"] == 0o600
    assert "opt-branch-host" in str(captured["db_file_text"])
    creds = captured["cred_file_json"]
    assert isinstance(creds, dict)
    assert {role: entry["model"] for role, entry in creds.items()} == expected_roles
    assert all(entry["api_key"] == "sk-proj-reflectkey9876543210" for entry in creds.values())
    assert all(entry["base_url"] == "https://reflect.example.com/v1" for entry in creds.values())
    assert creds["reflection_lm"]["api_key"] == "sk-proj-reflectkey9876543210"
    assert creds["reflection_lm"]["base_url"] == "https://reflect.example.com/v1"
    assert creds["reflection_lm"]["dialect"] == "openai_compatible"
    assert creds["reflection_lm"]["api_mode"] == "chat"
    assert not Path(captured["db_file"]).exists()  # type: ignore[arg-type]
    assert not Path(captured["cred_file"]).exists()  # type: ignore[arg-type]

    # Verify allowlisted subprocess env
    env = captured["env"]
    assert isinstance(env, dict)
    for forbidden in (
        "FLEET_API_BEARER_TOKEN",
        "FLEET_NEON_API_KEY",
        "FLEET_S3_ACCESS_KEY_ID",
        "FLEET_S3_SECRET_ACCESS_KEY",
        "FLEET_CREDENTIALS_ENCRYPTION_KEY",
        "DAYTONA_API_SERVICE_KEY",
        "DAYTONA_API_URL",
        "DAYTONA_V2_API_URL",
        "DAYTONA_API_KEY",
    ):
        assert forbidden not in env
    assert "primarysecret" not in env["FLEET_DATABASE_URL"]
    assert "primarysecret" not in env["FLEET_DATABASE_DIRECT_URL"]
    # RLM non-local run receives real FLEET_DAYTONA_API_KEY + workspace tuning overrides
    assert env["FLEET_DAYTONA_API_KEY"] == "dtn_host_secret_key_1234"
    assert env["FLEET_RLM_MAX_ITERS"] == "11"
    assert env["FLEET_HISTORY_MAX_TURNS"] == "4"
    assert env["FLEET_DAYTONA_EXEC_TIMEOUT_SECONDS"] == "77"

    # Verify recursive scrubbing of progress_json, report_json, and log_tail
    detail = (await client.get(f"/api/optimizations/jobs/{job_id}", headers=_AUTH)).json()
    dumped = json.dumps(detail)
    assert "reflectkey" not in dumped
    assert "optsecret" not in dumped
    assert "primarysecret" not in dumped
    assert "[Oversized log line omitted]" in detail["log_tail"]
    assert "[REDACTED]" in detail["log_tail"]
    assert "[REDACTED]" in detail["report_json"]["debug_note"]

    # Non-RLM target must NOT receive real Daytona API key in subprocess env
    skills_env = opt_runner.build_subprocess_env(
        opt_app.settings, target="skills", program_kind="rlm", local_interpreter=False
    )
    assert skills_env["FLEET_LLM_API_KEY"] == "disabled"
    assert skills_env.get("TAVILY_API_KEY") in (None, "available")
    assert skills_env["FLEET_DAYTONA_API_KEY"] != "dtn_host_secret_key_1234"


@pytest.mark.asyncio
async def test_single_sigterm_cancel_and_pre_spawn_cancel(opt_app, monkeypatch) -> None:
    client = opt_app.client
    mem_store = opt_app.store

    # 1. Running job: cancel endpoint sets flag+event; ONLY monitor_loop sends SIGTERM once!
    terminate_calls = 0
    proc_started = asyncio.Event()

    async def fake_long_exec(*_cmd, **_kwargs):
        class SlowProc:
            def __init__(self):
                self.returncode: int | None = None
                self.stdout = self
                self._done = asyncio.Event()

            async def readuntil(self, separator):
                await self._done.wait()
                raise asyncio.IncompleteReadError(b"", None)

            async def wait(self):
                await self._done.wait()
                return self.returncode

            def terminate(self):
                nonlocal terminate_calls
                terminate_calls += 1
                self.returncode = 130
                self._done.set()

            def kill(self):
                self.returncode = -9
                self._done.set()

        proc_started.set()
        return SlowProc()

    monkeypatch.setattr(opt_runner.asyncio, "create_subprocess_exec", fake_long_exec)

    launched = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={"target": "skills", "branch_mode": "configured_branch"},
    )
    assert launched.status_code == 201
    job_id = launched.json()["id"]
    await asyncio.wait_for(proc_started.wait(), timeout=2.0)

    # Call cancel twice rapidly -> still single SIGTERM!
    cancel1 = await client.post(f"/api/optimizations/jobs/{job_id}/cancel", headers=_AUTH)
    cancel2 = await client.post(f"/api/optimizations/jobs/{job_id}/cancel", headers=_AUTH)
    assert cancel1.status_code == 200
    assert cancel2.status_code == 200
    assert cancel1.json()["cancel_requested"] is True

    task = api.app.state.runtime.optimization_tasks.get(UUID(job_id))
    if task is not None:
        await asyncio.wait_for(task, timeout=2.0)

    assert terminate_calls == 1
    assert mem_store.jobs[UUID(job_id)].status == "cancelled"

    proc_started.clear()
    shutdown_job = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={"target": "skills", "branch_mode": "configured_branch"},
    )
    shutdown_job.raise_for_status()
    shutdown_id = UUID(shutdown_job.json()["id"])
    await asyncio.wait_for(proc_started.wait(), timeout=2.0)
    runtime = api.app.state.runtime
    await opt_runner.shutdown_optimization_runtime(runtime)
    assert mem_store.jobs[shutdown_id].status == "cancelled"
    assert terminate_calls == 2
    assert not runtime.optimization_tasks and not runtime.optimization_procs
    assert not runtime.optimization_cancel_events

    # 2. Pre-spawn cancellation: cancel requested BEFORE supervisor starts -> no branch/subprocess!
    branch_spy = AsyncMock(return_value=("br-should-not-run", "postgresql://u:p@h/db"))
    exec_spy = AsyncMock()
    monkeypatch.setattr(opt_runner, "create_ephemeral_neon_branch", branch_spy)
    monkeypatch.setattr(opt_runner.asyncio, "create_subprocess_exec", exec_spy)

    pre_job_id = uuid4()
    mem_store.jobs[pre_job_id] = db.OptimizationJob(
        id=pre_job_id,
        target="memory",
        status="queued",
        stage="cancelling",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        cancel_requested=True,
        created_at=datetime.now(UTC),
    )
    await opt_runner.supervise_optimization_job(
        api.app.state.runtime,
        pre_job_id,
        spec=opt_runner.JobRunSpec(target="memory"),
        branch_mode="auto_neon",
        effective=opt_app.settings,
        dataset_source="builtin",
        builtin_dataset_path=opt_app.settings.eval_datasets_dir / "memory.jsonl",
        custom_dataset=None,
        custom_database_url=None,
    )
    branch_spy.assert_not_awaited()
    exec_spy.assert_not_awaited()
    assert mem_store.jobs[pre_job_id].status == "cancelled"


@pytest.mark.asyncio
async def test_promote_flex_blocked_force_guard_per_target_reanchor_pinning_and_rollback(
    opt_app, monkeypatch, tmp_path
) -> None:
    client = opt_app.client
    mem_store = opt_app.store
    storage = opt_app.storage

    # Build valid MemoryGate and SkillSelector artifacts
    gate = dspy.Predict(agent.MemoryGate)
    gate_tmp = tmp_path / "gate_seed.json"
    gate.save(gate_tmp)
    gate_bytes = gate_tmp.read_bytes()
    gate_sha = hashlib.sha256(gate_bytes).hexdigest()

    selector = api.SkillRegistry.load(opt_app.settings.skills_dir).available_skills(
        opt_app.settings
    )
    from fleet_rlm.skills import build_skill_selector

    sel_pred = build_skill_selector(selector)
    sel_tmp = tmp_path / "sel_seed.json"
    sel_pred.save(sel_tmp)
    sel_bytes = sel_tmp.read_bytes()
    sel_sha = hashlib.sha256(sel_bytes).hexdigest()

    # 1. Flex job cannot be promoted -> 400 with exact message
    flex_job_id = uuid4()
    mem_store.jobs[flex_job_id] = db.OptimizationJob(
        id=flex_job_id,
        target="rlm",
        program_kind="flex",
        config_json={},
        progress_json={},
        cancel_requested=False,
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="rlm.jsonl",
        saved=True,
        artifact_object_key="optimizations/artifacts/flex/rlm.json",
        artifact_sha256=gate_sha,
        artifact_size_bytes=len(gate_bytes),
        report_json={"eligible": True, "saved": True},
        created_at=datetime.now(UTC),
    )
    flex_promo = await client.post(
        f"/api/optimizations/jobs/{flex_job_id}/promote", headers=_AUTH, json={}
    )
    assert flex_promo.status_code == 400
    assert (
        flex_promo.json()["detail"]
        == "dspy.Flex runs are evaluation-only; chat serving requires dspy.RLM artifacts."
    )

    # 2. --force ReAnchor run with eligible=False requires confirm_non_qualifying=True
    forced_id = uuid4()
    forced_key = "optimizations/artifacts/forced/memory.json"
    storage.objects[forced_key] = gate_bytes
    mem_store.jobs[forced_id] = db.OptimizationJob(
        id=forced_id,
        target="memory",
        config_json={},
        progress_json={},
        cancel_requested=False,
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        saved=True,
        baseline_score=0.70,
        optimized_score=0.71,
        artifact_object_key=forced_key,
        artifact_sha256=gate_sha,
        artifact_size_bytes=len(gate_bytes),
        report_json={"eligible": False, "saved": True},
        created_at=datetime.now(UTC),
    )
    blocked = await client.post(
        f"/api/optimizations/jobs/{forced_id}/promote",
        headers=_AUTH,
        json={"confirm_non_qualifying": False},
    )
    assert blocked.status_code == 409
    allowed = await client.post(
        f"/api/optimizations/jobs/{forced_id}/promote",
        headers=_AUTH,
        json={"confirm_non_qualifying": True},
    )
    assert allowed.status_code == 200

    # 3. Per-target ReAnchor pinning: skills uses Provider A, memory uses Provider B,
    #    and NEITHER clobbers workspace_settings.decision_model!
    prov_a = (
        await client.post(
            "/api/optimizations/providers",
            headers=_AUTH,
            json={
                "name": "Skills Provider A",
                "dialect": "openai_compatible",
                "base_url": "https://skills-a.example.com/v1",
                "api_key": "sk-proj-providerakey1111111111",
                "default_model": "skills-model-a",
            },
        )
    ).json()
    prov_b = (
        await client.post(
            "/api/optimizations/providers",
            headers=_AUTH,
            json={
                "name": "Memory Provider B",
                "dialect": "typesafe",
                "base_url": "https://api.typesafe.ai",
                "api_key": "ts_providerbkey2222222222",
                "default_model": "jev-1.15.0",
            },
        )
    ).json()

    skills_job_id = uuid4()
    skills_key = "optimizations/artifacts/skills-a/skills.json"
    storage.objects[skills_key] = sel_bytes
    mem_store.jobs[skills_job_id] = db.OptimizationJob(
        id=skills_job_id,
        target="skills",
        config_json={},
        progress_json={},
        cancel_requested=False,
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="skills.jsonl",
        saved=True,
        baseline_score=0.72,
        optimized_score=0.91,
        artifact_object_key=skills_key,
        artifact_sha256=sel_sha,
        artifact_size_bytes=len(sel_bytes),
        decision_provider_id=UUID(prov_a["id"]),
        resolved_decision_model="skills-model-a",
        report_json={"eligible": True, "saved": True},
        created_at=datetime.now(UTC),
    )

    mem_job_id = uuid4()
    mem_key = "optimizations/artifacts/mem-b/memory.json"
    storage.objects[mem_key] = gate_bytes
    mem_store.jobs[mem_job_id] = db.OptimizationJob(
        id=mem_job_id,
        target="memory",
        config_json={},
        progress_json={},
        cancel_requested=False,
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        saved=True,
        baseline_score=0.75,
        optimized_score=0.93,
        artifact_object_key=mem_key,
        artifact_sha256=gate_sha,
        artifact_size_bytes=len(gate_bytes),
        decision_provider_id=UUID(prov_b["id"]),
        resolved_decision_model="typesafe:jev-1.15.0",
        report_json={"eligible": True, "saved": True},
        created_at=datetime.now(UTC),
    )

    p_skills = await client.post(
        f"/api/optimizations/jobs/{skills_job_id}/promote", headers=_AUTH, json={}
    )
    assert p_skills.status_code == 200, p_skills.text
    assert p_skills.json()["decision_provider_id"] == prov_a["id"]
    assert p_skills.json()["decision_provider_name"] == "Skills Provider A"
    assert p_skills.json()["optimized_score"] == 0.91
    assert p_skills.json()["baseline_score"] == 0.72

    p_mem = await client.post(
        f"/api/optimizations/jobs/{mem_job_id}/promote", headers=_AUTH, json={}
    )
    assert p_mem.status_code == 200, p_mem.text
    assert p_mem.json()["decision_provider_id"] == prov_b["id"]
    assert p_mem.json()["decision_provider_name"] == "Memory Provider B"

    # Global workspace_settings.decision_model was NEVER mutated!
    assert "decision_model" not in mem_store.overrides

    # resolve_active_artifacts builds distinct LMs for skills and memory independently!
    built_calls: list[tuple[str, str, str, str | None, str | None]] = []

    def spy_build_lm(dialect, api_mode, model, api_key=None, base_url=None, **kwargs):
        built_calls.append((dialect, api_mode, model, api_key, base_url))
        return SimpleNamespace(model=f"{dialect}:{model}", api_base=base_url)

    monkeypatch.setattr(llm_registry, "build_dspy_lm", spy_build_lm)
    active = await opt_runner.resolve_active_artifacts(api.app.state.runtime, opt_app.settings)
    assert active.settings.skill_selector_path is not None
    assert active.settings.memory_gate_path is not None
    assert active.skills_decision_lm is not None
    assert active.memory_decision_lm is not None
    assert active.skills_decision_lm is not active.memory_decision_lm
    assert (
        "openai_compatible",
        "chat",
        "skills-model-a",
        "sk-proj-providerakey1111111111",
        "https://skills-a.example.com/v1",
    ) in built_calls
    assert (
        "typesafe",
        "auto",
        "typesafe:jev-1.15.0",
        "ts_providerbkey2222222222",
        "https://api.typesafe.ai",
    ) in built_calls

    # 4. Rollback history stepping cleanly backward across P3 -> P2 -> P1 -> default!
    mem_job_3 = uuid4()
    mem_key_3 = "optimizations/artifacts/mem-3/memory.json"
    storage.objects[mem_key_3] = gate_bytes
    mem_store.jobs[mem_job_3] = db.OptimizationJob(
        id=mem_job_3,
        target="memory",
        config_json={},
        progress_json={},
        cancel_requested=False,
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        saved=True,
        baseline_score=0.80,
        optimized_score=0.95,
        artifact_object_key=mem_key_3,
        artifact_sha256=gate_sha,
        artifact_size_bytes=len(gate_bytes),
        report_json={"eligible": True, "saved": True},
        created_at=datetime.now(UTC),
    )
    p3 = await client.post(f"/api/optimizations/jobs/{mem_job_3}/promote", headers=_AUTH, json={})
    assert p3.status_code == 200
    p3_id = p3.json()["id"]
    p2_id = p_mem.json()["id"]
    p1_id = allowed.json()["id"]

    # Overview exposes previous_promotion = P2 while active = P3
    ov_before = (await client.get("/api/optimizations/overview", headers=_AUTH)).json()
    mem_target = next(t for t in ov_before["targets"] if t["target"] == "memory")
    assert mem_target["active_promotion"]["id"] == p3_id
    assert mem_target["previous_promotion"]["id"] == p2_id
    assert mem_target["previous_promotion"]["decision_provider_name"] == "Memory Provider B"

    # Rollback 1: P3 -> P2
    rb1 = (
        await client.post(
            "/api/optimizations/targets/memory/rollback", headers=_AUTH, json={"mode": "previous"}
        )
    ).json()
    assert rb1["active_promotion"]["id"] == p2_id
    assert rb1["previous_promotion"]["id"] == p1_id

    # Rollback 2: P2 -> P1 (does NOT toggle back to P3!)
    rb2 = (
        await client.post(
            "/api/optimizations/targets/memory/rollback", headers=_AUTH, json={"mode": "previous"}
        )
    ).json()
    assert rb2["active_promotion"]["id"] == p1_id
    assert rb2["previous_promotion"] is None
    assert rb2["can_rollback_previous"] is False

    # Rollback 3: P1 -> default
    rb3 = (
        await client.post(
            "/api/optimizations/targets/memory/rollback", headers=_AUTH, json={"mode": "previous"}
        )
    ).json()
    assert rb3["source"] == "default"
    assert rb3["active_promotion"] is None
    assert "decision_model" not in mem_store.overrides


@pytest.mark.asyncio
async def test_neon_branch_cleanup_progress_partial_lines_mlflow_url_and_s3_fallback(
    opt_app, monkeypatch, tmp_path, caplog
) -> None:
    # 1. read_latest_progress does not advance past incomplete trailing line
    prog = tmp_path / "progress.jsonl"
    prog.write_bytes(b'{"stage": "s1"}\n{"stage": "s2"')
    offset, latest = opt_runner.read_latest_progress(prog, 0)
    assert latest == {"stage": "s1"}
    assert offset == len(b'{"stage": "s1"}\n')
    with prog.open("ab") as fh:
        fh.write(b', "completed": 3}\n')
    offset2, latest2 = opt_runner.read_latest_progress(prog, offset)
    assert latest2 == {"stage": "s2", "completed": 3}
    assert offset2 == prog.stat().st_size

    # 2. mlflow_run_url returns None instead of broken experiment '0' when experiment ID unknown
    runtime = api.app.state.runtime
    historical = opt_runner.mlflow_run_url(runtime, "fleet-rlm", "run-1")
    assert "#/experiments/7/runs/run-1" in historical
    separated = replace(runtime, mlflow_optimization_experiment_id="8")
    new = opt_runner.mlflow_run_url(separated, "fleet-rlm-optimization", "run-2")
    assert "#/experiments/8/runs/run-2" in new
    assert opt_runner.mlflow_run_url(runtime, "other-exp", "run-1") is None
    _swap_runtime_settings(opt_app.settings, mlflow_experiment_id=None)
    assert opt_runner.mlflow_run_url(api.app.state.runtime, "fleet-rlm", "run-1") is None
    _swap_runtime_settings(opt_app.settings, mlflow_experiment_id="7")

    # 3. Neon branch reads custom db/role from database_url, deletes branch on URI fetch failure,
    #    and logs loudly if DELETE returns HTTP 500
    neon_settings = opt_app.settings.model_copy(
        update={
            "database_url": "postgresql://custom_role:pw@ep-main.neon.tech/custom_db",
            "neon_api_key": SecretStr("napi_test_secret_1234"),
            "neon_project_id": "proj-xyz",
        }
    )
    requests_seen: list[tuple[str, str, dict[str, str]]] = []

    def neon_handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST":
            return httpx.Response(201, json={"branch": {"id": "br-leaked-1"}})
        if request.method == "GET":
            return httpx.Response(503, text="uri timeout")
        if request.method == "DELETE":
            return httpx.Response(500, text="delete failed")
        return httpx.Response(404)

    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(neon_handler)
    monkeypatch.setattr(
        opt_runner.httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=transport, **kw),
    )
    with caplog.at_level(logging.ERROR, logger="fleet_rlm.opt_runner"):
        with pytest.raises(httpx.HTTPStatusError):
            await opt_runner.create_ephemeral_neon_branch(neon_settings, "skills", uuid4())

    assert requests_seen[0][0] == "POST"
    assert requests_seen[1][0] == "GET"
    assert requests_seen[1][2]["database_name"] == "custom_db"
    assert requests_seen[1][2]["role_name"] == "custom_role"
    assert requests_seen[2][0] == "DELETE"
    assert requests_seen[2][1].endswith("/branches/br-leaked-1")
    assert any("HTTP 500" in rec.message for rec in caplog.records)

    # 4. Resilient S3 fallback in resolve_active_artifacts (logs warning, does not crash turns)
    opt_app.store.promotions.append(
        db.OptimizationPromotion(
            id=uuid4(),
            target="memory",
            job_id=uuid4(),
            artifact_object_key="optimizations/artifacts/missing/memory.json",
            artifact_sha256="f" * 64,
            artifact_size_bytes=99,
            active=True,
            promoted_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(opt_app.storage, "download", AsyncMock(side_effect=StorageError("S3 down")))
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.opt_runner"):
        active = await opt_runner.resolve_active_artifacts(api.app.state.runtime, opt_app.settings)
    assert active.settings.memory_gate_path is None
    assert any("falling back to env/default" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_job_stream_serializes_idle_ping_update_and_terminal_event(opt_app, monkeypatch):
    job = await opt_app.store.create_job(
        None,
        target="memory",
        program_kind="rlm",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        dataset_id=None,
        config_json={},
        mlflow_experiment="offline",
    )
    ticks = 0

    async def tick():
        nonlocal ticks
        ticks += 1
        if ticks == 31:
            job.stage = "evaluating"
        elif ticks == 32:
            job.status = "completed"

    monkeypatch.setattr(api.app.state.runtime.shutdown_event, "wait", tick)
    response = await opt_app.client.get(f"/api/optimizations/jobs/{job.id}/events", headers=_AUTH)
    response.raise_for_status()
    lines = response.text.splitlines()
    assert [line for line in lines if line.startswith("event: ")] == [
        "event: snapshot",
        "event: update",
        "event: done",
    ]
    assert ": ping" in lines
    assert [line for line in lines if line.startswith("id: ")] == [
        f"id: {job.id}:{index}" for index in range(3)
    ]
