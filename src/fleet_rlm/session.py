"""Per-Session runtime: one sandbox, harness, Skill manager, and engine, built on first use."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.skills import SkillManager
from fleet_rlm.store import Store
from fleet_rlm.store.db import VolumePostgresHarness
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id


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
    lease_lost: threading.Event = field(default_factory=threading.Event, repr=False)
    _lease_monitor_stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _lease_monitor: threading.Thread | None = field(default=None, repr=False)

    def assert_lease_held(self) -> None:
        """Fail closed after the dedicated connection holding this Session lock is lost."""
        if self.lease_lost.is_set():
            raise RuntimeError("Session ownership lease was lost; runtime must be rebuilt")

    def start_lease_monitor(self, *, interval_seconds: float = 10.0) -> None:
        """Probe the dedicated lease connection and cancel remote work when it becomes unhealthy."""
        lease = self.lease
        if lease is None or self._lease_monitor is not None:
            return

        def monitor() -> None:
            while not self._lease_monitor_stop.wait(interval_seconds):
                try:
                    lease.execute("SELECT 1")
                except Exception:
                    self.lease_lost.set()
                    with suppress(Exception):
                        self.sandbox_mgr.cancel_turn()
                    return

        self._lease_monitor = threading.Thread(
            target=monitor,
            name="fleet-session-lease",
            daemon=True,
        )
        self._lease_monitor.start()

    def close_lease(self) -> None:
        self._lease_monitor_stop.set()
        if self._lease_monitor is not None:
            self._lease_monitor.join(timeout=5)
            self._lease_monitor = None
        if self.lease is not None:
            self.lease.close()
            self.lease = None

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


def settings_for_session(settings: FleetSettings, saved: dict[str, Any]) -> FleetSettings:
    """Apply only the supported persisted mode/model overrides, never credentials or provider endpoints."""
    overrides = {}
    if "mode" in saved:
        overrides["execution_mode"] = saved["mode"]
    if saved.get("models"):
        overrides.update({f"fleet_{role}_model": saved["models"][role] for role in ("root", "sub")})
    return settings.with_explicit_overrides(**overrides)


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
                runtime.assert_lease_held()
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
        runtime.lease = self.store.acquire_session_lease(session_id)
        try:
            saved = self.store.runtime_settings(session_id)
            if "mode" in saved:
                runtime.set_mode(settings_for_session(settings, saved).execution_mode)
            if saved.get("models"):
                runtime.set_engine(self.engine_for_models(runtime, saved["models"]))
            if saved.get("context"):
                runtime.context = WorkspaceDataset(**saved["context"])
            runtime.start_lease_monitor()
        except BaseException:
            runtime.close_lease()
            raise
        return runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> AdaptiveFleetEngine:
        settings = settings_for_session(self.settings, {"models": models})
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
                runtime.close_lease()
