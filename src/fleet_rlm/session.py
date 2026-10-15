"""Per-Session runtime: one sandbox, harness, Skill manager, and engine, built on first use."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.paths import validate_session_id
from fleet_rlm.skills import SkillManager
from fleet_rlm.store import Store
from fleet_rlm.store.harness import VolumePostgresHarness
from fleet_rlm.workspace import WorkspaceDataset


@dataclass(slots=True)
class SessionRuntime:
    """All mutable runtime resources for one private Session.

    `turn_lock` serializes Turns and workspace changes. It is a threading lock: routes run in
    FastAPI's threadpool, and the streaming Turn route acquires it from a worker thread.
    """

    sandbox_mgr: DaytonaSandboxManager
    harness: VolumePostgresHarness
    skill_mgr: SkillManager
    engine: AdaptiveFleetEngine
    turn_lock: threading.Lock = field(default_factory=threading.Lock)
    execution_mode: str = "hybrid"
    context: WorkspaceDataset | None = None
    lease: psycopg.Connection | None = None
    configuration_overrides: dict[str, Any] = field(default_factory=dict)

    def set_mode(self, mode: str) -> None:
        self.execution_mode = mode
        self.engine.execution_mode = mode
        self.configuration_overrides["execution_mode"] = mode

    def set_engine(self, engine: AdaptiveFleetEngine) -> None:
        self.engine = engine
        self.sandbox_mgr.sub_lm = engine.sub_lm
        self.configuration_overrides.update(
            {"fleet_root_model": engine.root_lm.model, "fleet_sub_model": engine.sub_lm.model}
        )


def runtime_configuration(settings: FleetSettings, runtime: Any) -> dict[str, Any]:
    """The resolved configuration with this Session's model and mode overrides applied."""
    overrides = dict(getattr(runtime, "configuration_overrides", {}))
    for role in ("root", "sub"):
        model = getattr(getattr(runtime.engine, f"{role}_lm", None), "model", None)
        if model is not None and model != getattr(settings, f"fleet_{role}_model"):
            overrides[f"fleet_{role}_model"] = model
    mode = getattr(runtime, "execution_mode", settings.execution_mode)
    if mode != settings.execution_mode:
        overrides["execution_mode"] = mode
    return settings.configuration(overrides)


class SessionRuntimeRegistry:
    """Build a separate sandbox, namespace, and engine per Session, once."""

    def __init__(self, settings: FleetSettings, store: Store | None) -> None:
        self.settings = settings
        self.store = store
        self.root_lm: Any = None
        self.sub_lm: Any = None
        self.jev_lm: Any = None
        self._sessions: dict[str, SessionRuntime] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionRuntime:
        session_id = validate_session_id(session_id)
        with self._lock:
            runtime = self._sessions.get(session_id)
            if runtime is not None:
                return runtime
            if len(self._sessions) >= self.settings.max_active_sessions:
                raise RuntimeError("The maximum number of active Sessions has been reached")
            if self.store is None:
                raise RuntimeError("Session storage is not configured")
            if self.root_lm is None:
                self.root_lm, self.sub_lm, _, self.jev_lm = self.settings.build_lms()
            runtime = self._build(session_id)
            self._sessions[session_id] = runtime
            return runtime

    def _build(self, session_id: str) -> SessionRuntime:
        settings = self.settings
        sandbox_mgr = DaytonaSandboxManager(
            api_key=settings.fleet_daytona_api_key,
            org_id=settings.fleet_daytona_org_id,
            volume_id=settings.fleet_daytona_volume_id,
            shared_notes_subpath=settings.fleet_shared_notes_subpath,
            session_id=session_id,
            snapshot=settings.fleet_daytona_snapshot,
            auto_stop_minutes=settings.daytona_auto_stop_minutes,
            sub_lm=self.sub_lm,
            jev_lm=self.jev_lm,
            max_llm_calls=settings.max_llm_calls_per_turn,
            max_depth=settings.max_agent_depth,
            max_children=settings.max_child_sandboxes,
            max_active_children=settings.max_active_children,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            linked_children=settings.linked_children,
        )
        assert self.store is not None
        harness = VolumePostgresHarness(self.store, sandbox_mgr)
        runtime = SessionRuntime(
            sandbox_mgr=sandbox_mgr,
            harness=harness,
            skill_mgr=SkillManager(sandbox_mgr, Path(settings.local_skills_dir)),
            engine=AdaptiveFleetEngine(
                sandbox_mgr=sandbox_mgr,
                root_lm=self.root_lm,
                sub_lm=self.sub_lm,
                jev_lm=self.jev_lm,
                compiled_path=str(Path(settings.compiled_program_dir) / f"{session_id}.json"),
                execution_mode=settings.execution_mode,
            ),
            execution_mode=settings.execution_mode,
        )
        harness.ensure_session(session_id)
        runtime.lease = harness.acquire_session_lease()
        try:
            saved = harness.runtime_settings()
            if "mode" in saved:
                runtime.set_mode(saved["mode"])
            if saved.get("models"):
                runtime.set_engine(self.engine_for_models(runtime, saved["models"]))
            if saved.get("context"):
                runtime.context = WorkspaceDataset(**saved["context"])
        except BaseException:
            runtime.lease.close()
            raise
        return runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> AdaptiveFleetEngine:
        settings = self.settings.model_copy(
            update={"fleet_root_model": models["root"], "fleet_sub_model": models["sub"]}
        )
        root, sub, _, _ = settings.build_lms()
        return AdaptiveFleetEngine(
            sandbox_mgr=runtime.sandbox_mgr,
            root_lm=root,
            sub_lm=sub,
            jev_lm=self.jev_lm,
            compiled_path=runtime.engine.compiled_path,
            execution_mode=runtime.execution_mode,
        )

    def close(self) -> None:
        with self._lock:
            runtimes = tuple(self._sessions.values())
            self._sessions.clear()
        try:
            if runtimes:
                with ThreadPoolExecutor(max_workers=min(8, len(runtimes))) as pool:
                    list(pool.map(lambda runtime: runtime.sandbox_mgr.shutdown(), runtimes))
        finally:
            for runtime in runtimes:
                if runtime.lease is not None:
                    runtime.lease.close()
