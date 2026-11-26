"""Tests for the Optimization Control Plane REST, SSE, storage, and safety endpoints."""

import asyncio
import hashlib
import importlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import dspy
import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from fleet_rlm import agent, api, db
from fleet_rlm.config import Settings
from fleet_rlm.storage import ObjectStorage

_AUTH = {"Authorization": "Bearer opt-token"}


class MemoryOptStore:
    """In-memory DB adapter mimicking Postgres optimization tables & workspace settings."""

    def __init__(self) -> None:
        self.datasets: dict[UUID, db.OptimizationDataset] = {}
        self.jobs: dict[UUID, db.OptimizationJob] = {}
        self.promotions: list[db.OptimizationPromotion] = []
        self.overrides: dict[str, object] = {}

    async def get_overrides(self, _sessionmaker) -> dict[str, object]:
        return dict(self.overrides)

    async def update_overrides(
        self, _sessionmaker, changes: dict[str, object]
    ) -> dict[str, object]:
        for k, v in changes.items():
            if v is None:
                self.overrides.pop(k, None)
            else:
                self.overrides[k] = v
        return dict(self.overrides)

    async def reset_overrides(self, _sessionmaker) -> dict[str, object]:
        self.overrides.clear()
        return {}

    async def create_dataset(self, _sessionmaker, **kwargs) -> db.OptimizationDataset:
        for existing in self.datasets.values():
            if existing.name == kwargs["name"]:
                raise RuntimeError("duplicate")
        ds = db.OptimizationDataset(
            id=kwargs.get("dataset_id") or uuid4(),
            name=kwargs["name"],
            target=kwargs["target"],
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

    async def delete_dataset(
        self, _sessionmaker, dataset_id: UUID
    ) -> db.OptimizationDataset | None:
        ds = self.datasets.get(dataset_id)
        if ds is None:
            return None
        for job in self.jobs.values():
            if job.dataset_id == dataset_id and job.status in {"queued", "running"}:
                raise db.OptimizationConflictError("Dataset is currently in use by an active job")
        return self.datasets.pop(dataset_id)

    async def create_job(self, _sessionmaker, **kwargs) -> db.OptimizationJob:
        for existing in self.jobs.values():
            if existing.target == kwargs["target"] and existing.status in {"queued", "running"}:
                raise db.OptimizationConflictError(
                    f"Target '{kwargs['target']}' already has an active optimization job"
                )
        job = db.OptimizationJob(
            id=uuid4(),
            target=kwargs["target"],
            program_kind=kwargs["program_kind"],
            status="queued",
            stage="queued",
            dataset_source=kwargs["dataset_source"],
            dataset_name=kwargs["dataset_name"],
            dataset_id=kwargs["dataset_id"],
            config_json=kwargs["config_json"],
            progress_json={},
            log_tail="",
            cancel_requested=False,
            mlflow_experiment=kwargs["mlflow_experiment"],
            created_at=datetime.now(UTC),
        )
        self.jobs[job.id] = job
        return job

    async def update_progress(
        self, _sessionmaker, job_id: UUID, **kwargs
    ) -> db.OptimizationJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return None
        for k, v in kwargs.items():
            if v is not None:
                setattr(job, k, v)
        return job

    async def finish_job(self, _sessionmaker, job_id: UUID, **kwargs) -> db.OptimizationJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return None
        for k, v in kwargs.items():
            if v is not None:
                setattr(job, k, v)
        job.finished_at = datetime.now(UTC)
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
            ref = job.started_at or job.created_at
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
        rows.sort(key=lambda j: j.created_at, reverse=True)
        return rows[:limit]

    async def get_job(self, _sessionmaker, job_id: UUID) -> db.OptimizationJob | None:
        return self.jobs.get(job_id)

    async def promote(self, _sessionmaker, **kwargs) -> db.OptimizationPromotion:
        for p in self.promotions:
            if p.target == kwargs["target"] and p.active:
                p.active = False
                p.rolled_back_at = datetime.now(UTC)
        promo = db.OptimizationPromotion(
            id=uuid4(),
            target=kwargs["target"],
            job_id=kwargs["job_id"],
            artifact_object_key=kwargs["artifact_object_key"],
            artifact_sha256=kwargs["artifact_sha256"],
            artifact_size_bytes=kwargs["artifact_size_bytes"],
            resolved_decision_model=kwargs["resolved_decision_model"],
            active=True,
            promoted_at=datetime.now(UTC),
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
        inactive = [
            p for p in self.promotions if p.target == target and p.id != active.id and not p.active
        ]
        if not inactive:
            return None
        inactive.sort(key=lambda p: p.promoted_at, reverse=True)
        prev = inactive[0]
        prev.active = True
        prev.rolled_back_at = None
        return prev

    async def list_active_promotions(self, _sessionmaker) -> dict[str, db.OptimizationPromotion]:
        return {p.target: p for p in self.promotions if p.active}

    async def list_promotion_history(self, _sessionmaker, target: str, limit: int = 10):
        rows = [p for p in self.promotions if p.target == target]
        rows.sort(key=lambda p: p.promoted_at, reverse=True)
        return rows[:limit]


class FakeStorage:
    optimization_artifact_key = staticmethod(ObjectStorage.optimization_artifact_key)
    optimization_dataset_key = staticmethod(ObjectStorage.optimization_dataset_key)

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def close(self) -> None:
        pass

    async def put_object(self, key: str, path: Path) -> tuple[str, int]:
        raw = path.read_bytes()
        self.objects[key] = raw
        return hashlib.sha256(raw).hexdigest(), len(raw)

    async def download(self, key: str, path: Path, *, sha256: str, size: int) -> None:
        raw = self.objects[key]
        assert hashlib.sha256(raw).hexdigest() == sha256
        assert len(raw) == size
        path.write_bytes(raw)

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


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
        database_url="postgresql://primary-host/neondb",
        database_direct_url="postgresql://primary-host/neondb",
        optimization_database_url="postgresql://opt-branch-host/neondb",
        daytona_api_key="offline",
        daytona_image="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri="http://127.0.0.1:5001",
        api_bearer_token="opt-token",
        skills_dir=skills_dir,
        eval_datasets_dir=eval_dir,
        artifact_cache_dir=cache_dir,
    )

    mem_store = MemoryOptStore()
    storage = FakeStorage()
    session_factory = SimpleNamespace()

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

    monkeypatch.setattr(api, "Settings", lambda: settings)
    monkeypatch.setattr(api, "ObjectStorage", lambda _s: storage)
    monkeypatch.setattr(api, "make_daytona_client", daytona_ctx)
    monkeypatch.setattr(
        api.store, "create_db", lambda _s: (SimpleNamespace(dispose=AsyncMock()), session_factory)
    )
    monkeypatch.setattr(api.store, "reap_stale_runs", AsyncMock(return_value=0))
    monkeypatch.setattr(api.store, "get_workspace_settings_overrides", mem_store.get_overrides)
    monkeypatch.setattr(
        api.store, "update_workspace_settings_overrides", mem_store.update_overrides
    )
    monkeypatch.setattr(api.store, "reset_workspace_settings_overrides", mem_store.reset_overrides)
    monkeypatch.setattr(api.store, "create_optimization_dataset", mem_store.create_dataset)
    monkeypatch.setattr(api.store, "list_optimization_datasets", mem_store.list_datasets)
    monkeypatch.setattr(api.store, "get_optimization_dataset", mem_store.get_dataset)
    monkeypatch.setattr(api.store, "delete_optimization_dataset", mem_store.delete_dataset)
    monkeypatch.setattr(api.store, "create_optimization_job", mem_store.create_job)
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
    monkeypatch.setattr(
        api.mlflow, "set_experiment", lambda *_a, **_k: SimpleNamespace(experiment_id="7")
    )
    monkeypatch.setattr(api.mlflow.dspy, "autolog", lambda *_a, **_k: None)

    async with api.lifespan(api.app):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, mem_store, storage, settings


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
async def test_datasets_discovery_preview_upload_secret_guard_and_delete(opt_app) -> None:
    client, _store, storage, _settings = opt_app

    listed = (await client.get("/api/optimizations/datasets", headers=_AUTH)).json()
    assert [item["id"] for item in listed] == ["builtin:skills", "builtin:memory", "builtin:rlm"]

    preview = await client.get(
        "/api/optimizations/datasets/builtin:skills/preview?limit=1", headers=_AUTH
    )
    assert preview.status_code == 200
    assert len(preview.json()["rows"]) == 1

    # Secret pattern rejection
    bad_secret = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "skills"},
        files={
            "file": (
                "leak.jsonl",
                b'{"task": "sk-secretsecretsecret12345", "needs_skill": false}\n',
                "text/plain",
            )
        },
    )
    assert bad_secret.status_code == 422

    # Unknown skill rejection
    bad_skill = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "skills"},
        files={
            "file": (
                "unknown.jsonl",
                b'{"task": "Run", "needs_skill": true, "expected_skill": "missing"}\n',
                "text/plain",
            )
        },
    )
    assert bad_skill.status_code == 422

    # Valid custom dataset upload + preview + delete
    valid = await client.post(
        "/api/optimizations/datasets",
        headers=_AUTH,
        data={"target": "memory", "name": "custom-mem.jsonl"},
        files={
            "file": (
                "custom-mem.jsonl",
                (
                    b'{"task": "Remember timezone UTC", "worth_saving": true, '
                    b'"lineage": "tz", "split": "train"}\n'
                ),
                "application/x-ndjson",
            )
        },
    )
    assert valid.status_code == 201
    ds_id = valid.json()["id"]
    assert len(storage.objects) == 1

    ds_preview = await client.get(f"/api/optimizations/datasets/{ds_id}/preview", headers=_AUTH)
    assert ds_preview.status_code == 200
    assert ds_preview.json()["rows"][0]["task"] == "Remember timezone UTC"

    # Cannot delete builtin
    del_builtin = await client.delete("/api/optimizations/datasets/builtin:skills", headers=_AUTH)
    assert del_builtin.status_code == 400

    # Delete custom dataset cleans S3 too
    deleted = await client.delete(f"/api/optimizations/datasets/{ds_id}", headers=_AUTH)
    assert deleted.status_code == 204
    assert len(storage.objects) == 0


@pytest.mark.asyncio
async def test_branch_safety_refuses_primary_database(opt_app) -> None:
    client, *_ = opt_app

    # Custom URL matching primary fails
    resp = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={
            "target": "skills",
            "branch_mode": "custom_branch",
            "custom_database_url": "postgresql://primary-host/neondb",
        },
    )
    assert resp.status_code == 422

    # Direct mode without opt-in fails
    resp2 = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={"target": "skills", "branch_mode": "direct_readonly", "allow_direct_opt_in": False},
    )
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_job_launch_sse_lock_promotion_rollback_and_live_cache(
    opt_app, monkeypatch, tmp_path
) -> None:
    client, mem_store, storage, _settings = opt_app

    # Build a valid MemoryGate artifact bytes payload matching dspy.Module.save()
    gate = dspy.Predict(agent.MemoryGate)
    gate_tmp = tmp_path / "gate_seed.json"
    gate.save(gate_tmp)
    gate_bytes = gate_tmp.read_bytes()

    # Fake subprocess emitting progress + report + artifact and logging a secret URL.
    async def fake_exec(*cmd, **kwargs):
        cmd_list = list(cmd)
        out_path = Path(cmd_list[cmd_list.index("--output") + 1])
        prog_path = Path(cmd_list[cmd_list.index("--progress-file") + 1])
        rep_path = Path(cmd_list[cmd_list.index("--report-file") + 1])

        assert "DAYTONA_API_URL" not in kwargs["env"]
        assert "DAYTONA_V2_API_URL" not in kwargs["env"]

        prog_path.write_text(
            json.dumps(
                {"stage": "reanchor_cv", "label": "Running ReAnchor", "completed": 5, "total": None}
            )
            + "\n",
            encoding="utf-8",
        )
        out_path.write_bytes(gate_bytes)
        rep_path.write_text(
            json.dumps(
                {
                    "target": "memory",
                    "saved": True,
                    "baseline_score": 0.75,
                    "optimized_score": 0.90,
                    "train_size": 70,
                    "holdout_size": 30,
                    "mlflow_run_id": "run-abc-123",
                    "report": {
                        "eligible": True,
                        "saved": True,
                        "fp_rate_before": 0.2,
                        "fp_rate_after": 0.05,
                        "resolved_decision_model": "typesafe:jev-1.14.0",
                        "fitted_parameters": {"worth_saving": {"threshold": 0.55}},
                    },
                }
            ),
            encoding="utf-8",
        )

        class FakeProc:
            returncode = None

            def __init__(self):
                self.stdout = self
                self._lines = [
                    b"connecting postgresql://secretuser:secretpass@opt-branch-host/neondb\n",
                    b"[memory] saved\n",
                ]

            async def readline(self):
                await asyncio.sleep(0.05)
                if self._lines:
                    return self._lines.pop(0)
                return b""

            async def wait(self):
                await asyncio.sleep(0.6)
                self.returncode = 0
                return 0

            def terminate(self):
                self.returncode = 130

            def kill(self):
                self.returncode = -9

        return FakeProc()

    monkeypatch.setattr(api.asyncio, "create_subprocess_exec", fake_exec)

    # 404 on unknown SSE stream before headers start
    missing_sse = await client.get(f"/api/optimizations/jobs/{uuid4()}/events", headers=_AUTH)
    assert missing_sse.status_code == 404

    started = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={
            "target": "memory",
            "dataset_id": "builtin:memory",
            "branch_mode": "configured_branch",
        },
    )
    assert started.status_code == 201
    job_id = started.json()["id"]

    # Concurrent second launch for same target returns 409
    conflict = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={
            "target": "memory",
            "dataset_id": "builtin:memory",
            "branch_mode": "configured_branch",
        },
    )
    assert conflict.status_code == 409

    # Consume SSE until done
    sse_resp = await client.get(f"/api/optimizations/jobs/{job_id}/events", headers=_AUTH)
    assert sse_resp.status_code == 200
    assert "event: done" in sse_resp.text

    detail = (await client.get(f"/api/optimizations/jobs/{job_id}", headers=_AUTH)).json()
    assert detail["status"] == "completed"
    assert detail["optimized_score"] == 0.9
    assert detail["resolved_decision_model"] == "typesafe:jev-1.14.0"
    assert "secretpass" not in detail["log_tail"]
    assert "[REDACTED]" in detail["log_tail"]
    assert "/runs/run-abc-123" in (detail["mlflow_run_url"] or "")

    # Promote to live traffic
    promoted = await client.post(
        f"/api/optimizations/jobs/{job_id}/promote", headers=_AUTH, json={}
    )
    assert promoted.status_code == 200, promoted.text
    assert mem_store.overrides.get("decision_model") == "typesafe:jev-1.14.0"

    # Settings reset must NOT clear promoted artifact!
    await client.delete("/api/settings", headers=_AUTH)
    ov = (await client.get("/api/optimizations/overview", headers=_AUTH)).json()
    mem_status = next(t for t in ov["targets"] if t["target"] == "memory")
    assert mem_status["source"] == "promoted"

    # Live artifact resolver picks up promoted memory gate without worker restart
    runtime = api.app.state.runtime
    resolved_settings, _ = await api._resolve_active_artifacts(runtime, runtime.settings)
    assert resolved_settings.memory_gate_path is not None
    assert resolved_settings.memory_gate_path.is_file()

    # Rollback to env/default clears active promotion
    rolled = await client.post(
        "/api/optimizations/targets/memory/rollback",
        headers=_AUTH,
        json={"mode": "env"},
    )
    assert rolled.status_code == 200
    assert rolled.json()["source"] == "default"

    # Non-qualifying run requires confirm_non_qualifying=true
    discarded_id = uuid4()
    art_key = "optimizations/artifacts/force-test/memory.json"
    mem_store.jobs[discarded_id] = db.OptimizationJob(
        id=discarded_id,
        target="memory",
        status="completed",
        stage="completed",
        dataset_source="builtin",
        dataset_name="memory.jsonl",
        saved=True,
        artifact_object_key=art_key,
        artifact_sha256=hashlib.sha256(gate_bytes).hexdigest(),
        artifact_size_bytes=len(gate_bytes),
        report_json={"eligible": False, "saved": True},
        created_at=datetime.now(UTC),
    )
    storage.objects[art_key] = gate_bytes
    blocked = await client.post(
        f"/api/optimizations/jobs/{discarded_id}/promote",
        headers=_AUTH,
        json={"confirm_non_qualifying": False},
    )
    assert blocked.status_code == 409
    forced = await client.post(
        f"/api/optimizations/jobs/{discarded_id}/promote",
        headers=_AUTH,
        json={"confirm_non_qualifying": True},
    )
    assert forced.status_code == 200


@pytest.mark.asyncio
async def test_stale_reap_and_ephemeral_neon_branch_cleanup_on_error(opt_app, monkeypatch) -> None:
    client, mem_store, _storage, settings = opt_app

    stale_id = uuid4()
    mem_store.jobs[stale_id] = db.OptimizationJob(
        id=stale_id,
        target="skills",
        status="running",
        stage="reanchor_cv",
        dataset_source="builtin",
        dataset_name="skills.jsonl",
        created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    reaped = await api.store.reap_stale_optimization_jobs(None, 3600)
    assert reaped == 1
    assert mem_store.jobs[stale_id].status == "failed"

    # Mock Neon REST API branch create + guaranteed delete in finally
    deleted_branches: list[str] = []
    settings_with_neon = settings.model_copy(
        update={"neon_api_key": SecretStr("neon-secret"), "neon_project_id": "proj-123"}
    )
    api.app.state.runtime = api.Runtime(
        settings=settings_with_neon,
        sessionmaker=api.app.state.runtime.sessionmaker,
        daytona=api.app.state.runtime.daytona,
        storage=api.app.state.runtime.storage,
        skills=api.app.state.runtime.skills,
        lm=api.app.state.runtime.lm,
        sub_lm=api.app.state.runtime.sub_lm,
        decision_lm=api.app.state.runtime.decision_lm,
        turn_semaphore=api.app.state.runtime.turn_semaphore,
        optimized_program_state=None,
        artifact_cache_dir=api.app.state.runtime.artifact_cache_dir,
    )

    monkeypatch.setattr(
        api,
        "_create_ephemeral_neon_branch",
        AsyncMock(return_value=("br-temp-999", "postgresql://temp@ep-999/neondb")),
    )

    async def fake_delete(_s, branch_id: str) -> None:
        deleted_branches.append(branch_id)

    monkeypatch.setattr(api, "_delete_ephemeral_neon_branch", fake_delete)
    monkeypatch.setattr(
        api.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    started = await client.post(
        "/api/optimizations/jobs",
        headers=_AUTH,
        json={"target": "skills", "branch_mode": "auto_neon"},
    )
    assert started.status_code == 201
    job_id = started.json()["id"]
    await asyncio.sleep(0.1)
    assert deleted_branches == ["br-temp-999"]
    failed = (await client.get(f"/api/optimizations/jobs/{job_id}", headers=_AUTH)).json()
    assert failed["status"] == "failed"
