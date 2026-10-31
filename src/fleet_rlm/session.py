"""Per-Session runtime: sandbox, durable state policies, Skills, and engine, built on first use."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.skills import SkillManager
from fleet_rlm.store import IdempotencyConflictError, Store
from fleet_rlm.workspace import WorkspaceDataset, _workspace_relative_path, validate_session_id

_LOGGER = logging.getLogger("fleet_rlm.session")
_EVICT_WAIT_SECONDS = 2.0
_LEASE_MONITOR_JOIN_SECONDS = 5.0


_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000
_MAX_STORED_TURNS = 200


@dataclass(slots=True)
class SessionRuntime:
    """All mutable runtime resources for one private Session.

    `turn_lock` serializes Turns and workspace changes. It is a threading lock: routes run in
    FastAPI's threadpool, and the streaming Turn route acquires it from a worker thread.
    """

    sandbox_mgr: DaytonaSandboxManager
    store: Store
    skill_mgr: SkillManager
    engine: AdaptiveFleetEngine
    turn_lock: threading.Lock = field(default_factory=threading.Lock)
    context: WorkspaceDataset | None = None
    lease: psycopg.Connection | None = None
    ownership_epoch: int | None = None
    configuration_overrides: dict[str, Any] = field(default_factory=dict)
    lease_lost: threading.Event = field(default_factory=threading.Event, repr=False)
    restart_requested: threading.Event = field(default_factory=threading.Event, repr=False)
    _lease_monitor_stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _lease_monitor: threading.Thread | None = field(default=None, repr=False)
    _lease_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def session_id(self) -> str:
        """Return the Session ID owned by the sandbox manager."""
        return self.sandbox_mgr.session_id

    def ensure_session(self, session_id: str) -> None:
        """Create the Session row after validating its ID."""
        self.store.ensure_session(validate_session_id(session_id))

    def save_memory(
        self,
        memory_id: str,
        content: str,
        *,
        sources: dict[str, str] | None = None,
        kind: str = "observation",
        reviewed: bool = False,
    ) -> None:
        """Validate and save one memory, checking content, kind, and source paths."""
        validate_session_id(memory_id)
        if (
            not content.strip()
            or len(content) > 10000
            or kind not in {"observation", "preference", "hypothesis", "procedure"}
        ):
            raise ValueError("Invalid memory content or kind")
        sources = sources or {}
        if len(sources) > 100 or any(not self._valid_vfs_path(p) for p in sources):
            raise ValueError("Invalid memory sources")
        self.store.save_memory(self.session_id, memory_id, content, kind, sources, reviewed)

    def memories(self, query: str = "", *, reviewed_only: bool = False) -> list[dict[str, Any]]:
        """Search memories with a bounded OR-of-words query, optionally only reviewed ones."""
        search = " OR ".join(query[:2000].split()[:32])
        return self.store.memories(self.session_id, search, reviewed_only=reviewed_only)

    def delete_memory(self, memory_id: str) -> None:
        """Delete one memory after validating its ID."""
        self.store.delete_memory(self.session_id, validate_session_id(memory_id))

    def claim_turn(self, turn_id: str, key: str, task: str) -> dict[str, Any] | None:
        """Persist intent before side effects; an uncertain running turn is never replayed."""
        digest = hashlib.sha256(task.encode()).hexdigest()
        ownership = {"ownership_epoch": self.ownership_epoch} if self.ownership_epoch is not None else {}
        prior = self.store.claim_turn(self.session_id, turn_id, key, digest, **ownership)
        if prior is not None and prior["task_sha256"] != digest:
            raise IdempotencyConflictError("Idempotency key already belongs to another task")
        return prior

    def turn_status(self, turn_id: str) -> dict[str, Any] | None:
        """Return one Turn's status after validating its ID."""
        return self.store.turn_status(self.session_id, validate_session_id(turn_id))

    def load_context(self, session_id: str) -> tuple[str, str]:
        """The Session's memory summary and bounded rendered history."""
        summary, turns = self.store.load_session(validate_session_id(session_id))
        return summary, self._render_history(turns)

    @staticmethod
    def _render_history(turns: list[dict[str, Any]]) -> str:
        if not isinstance(turns, list):
            return ""
        entries: list[str] = []
        total_chars = 0
        for turn in reversed(turns[-_SESSION_HISTORY_TURNS:]):
            if not isinstance(turn, dict):
                continue
            task = str(turn.get("task") or "")
            answer = str(turn.get("answer") or "")
            rendered = f"User: {task}\nAssistant: {answer}"
            separator_chars = 2 if entries else 0
            remaining = _SESSION_HISTORY_CHARS - total_chars - separator_chars
            header = "User: \nAssistant: "
            if remaining < len(header) + 2:
                break
            if len(rendered) > remaining:
                task_budget = min(len(task), 4_000, (remaining - len(header)) // 2)
                task_excerpt = task[:task_budget]
                if len(task) > task_budget:
                    task_excerpt += "…"
                answer_budget = remaining - len(header) - len(task_excerpt)
                if len(answer) > answer_budget:
                    answer_excerpt = "…" + answer[-(answer_budget - 1) :] if answer_budget > 1 else "…"
                else:
                    answer_excerpt = answer
                rendered = f"User: {task_excerpt}\nAssistant: {answer_excerpt}"
            entries.append(rendered)
            total_chars += len(entries[-1])
        return "\n\n".join(reversed(entries))

    def commit_turn(
        self,
        session_id: str,
        turn_record: dict[str, Any],
        new_memory_note: str = "",
        replay_snapshot: tuple[str, bytes] | None = None,
        *,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Commit and return the canonical completion, including the current artifact inventory.

        Commits the record, memory note, completion payload, and optional immutable replay
        together. `replay_snapshot` is the digest and compressed bytes selected by the record.
        `artifact_paths` optionally supplies a captured workspace view; by default the
        current workspace is listed. Both paths use the same validation and sorting.

        Raises:
            ValueError: Replay selection metadata or digest is inconsistent, the Turn ID is
                missing, the Session ID is invalid, the record is oversized, or a workspace
                path is invalid.

        The returned payload owns its nested values independently of the record and Store.
        Serialization, workspace listing, and storage errors propagate.
        """
        if bool(turn_record.get("replay_snapshot_selected")) != (replay_snapshot is not None):
            raise ValueError("Selected replay metadata and immutable snapshot must be committed together")
        if replay_snapshot is not None and turn_record.get("replay_snapshot_sha256") != replay_snapshot[0]:
            raise ValueError("Turn replay digest does not match the immutable snapshot")
        if not turn_record.get("turn_id"):
            raise ValueError("Turn record must carry a turn_id")
        turn_record = deepcopy(turn_record)
        session_id = validate_session_id(session_id)
        record_json = json.dumps(turn_record, ensure_ascii=False, separators=(",", ":"))
        if len(record_json.encode("utf-8")) > _MAX_TURN_RECORD_BYTES:
            raise ValueError("Turn record exceeds the persistence size limit")
        paths = self.sandbox_mgr.list_workspace_files() if artifact_paths is None else artifact_paths
        if not isinstance(paths, list) or any(not self._valid_vfs_path(path) for path in paths):
            raise ValueError("Workspace listing returned an invalid relative path")
        artifacts = sorted({path for path in paths if path.startswith("artifacts/")})
        capture = turn_record.get("artifact_capture")
        if capture is not None and (
            not isinstance(capture, dict)
            or not isinstance(capture.get("complete"), bool)
            or capture.get("paths") != artifacts
        ):
            raise ValueError("Canonical artifact inventory does not match its captured view")
        from fleet_rlm.sandbox.snapshots import PROVENANCE_KEYS

        result = {
            "event": "turn_complete",
            "turn_id": turn_record["turn_id"],
            "answer": turn_record.get("answer", ""),
            "route": turn_record.get("route", ""),
            "learned_skill_note": turn_record.get("learned_skill_note", ""),
            "artifacts": artifacts,
            **{
                key: turn_record[key]
                for key in ("lm_calls", "sandbox_errors", "submit_rejections", "budget_breakdown")
                if key in turn_record
            },
            # The status payload carries the same provider-resolved identity as the record.
            **{key: turn_record[key] for key in PROVENANCE_KEYS if key in turn_record},
            # Deadline, outcome, and wall duration join the same turn_id across the terminal
            # event, the stored record, and the fleet_turn span. `effective_seconds=None` is
            # preserved explicitly rather than substituted with a default.
            **{
                key: turn_record[key]
                for key in (
                    "deadline_source",
                    "effective_seconds",
                    "outcome",
                    "duration_seconds",
                )
                if key in turn_record
            },
        }
        if turn_record.get("trace_id"):
            result["trace_id"] = turn_record["trace_id"]
        # The status payload reports the same teardown outcome as the stored Turn and span.
        if turn_record.get("cleanup"):
            result["cleanup"] = turn_record["cleanup"]
        # Provider-owned lifecycle bounds are surfaced unchanged; they are not Fleet limits.
        for key in ("ttl_minutes", "auto_stop_interval", "maximum_lifespan_minutes", "provider_owned"):
            if key in turn_record:
                result[key] = turn_record[key]
        # The live root handle this Turn used, when it provisioned one.
        if turn_record.get("root_sandbox_id"):
            result["root_sandbox_id"] = turn_record["root_sandbox_id"]
        completion = (turn_record["turn_id"], deepcopy(result))
        self.store.commit_turn(
            session_id,
            turn_record_json=record_json,
            max_turns=_MAX_STORED_TURNS,
            memory_note=new_memory_note,
            completion=completion,
            replay_snapshot=replay_snapshot,
            **({"ownership_epoch": self.ownership_epoch} if self.ownership_epoch is not None else {}),
        )
        return result

    @staticmethod
    def _valid_vfs_path(rel_path: str) -> bool:
        try:
            _workspace_relative_path(rel_path)
        except ValueError:
            return False
        return True

    def assert_lease_held(self) -> None:
        """Fail closed after the dedicated connection holding this Session lock is lost."""
        if self.lease_lost.is_set():
            raise RuntimeError("Session ownership lease was lost; runtime must be rebuilt")

    def start_lease_monitor(self, *, interval_seconds: float = 10.0) -> None:
        """Start periodic lease probes, renewing a dropped connection before declaring ownership lost.

        `interval_seconds` is the delay between probes. A failed reacquisition marks the lease
        lost and requests Turn cancellation; cancellation failures are suppressed. Calls are
        no-ops when no lease exists or a monitor has already been created.
        """
        lease = self.lease
        if lease is None or self._lease_monitor is not None:
            return

        def monitor() -> None:
            """Renew a dropped Session lease or mark it lost and request cancellation if renewal fails."""
            current = lease
            while not self._lease_monitor_stop.wait(interval_seconds):
                try:
                    current.execute("SELECT 1")
                    continue
                except Exception:
                    pass
                # The connection dropped (Lakebase closes idle ones). Re-take the advisory lock on a fresh
                # connection, which also carries a fresh credential; another worker holding it means we lost.
                try:
                    renewed = self.store.acquire_session_lease(self.sandbox_mgr.session_id)
                except Exception:
                    if self._lease_monitor_stop.is_set():
                        return
                    self.lease_lost.set()
                    with suppress(Exception):
                        self.sandbox_mgr.request_cancellation()
                    return
                with self._lease_lock:
                    if self._lease_monitor_stop.is_set():
                        with suppress(Exception):
                            renewed.close()
                        return
                    self.lease = renewed
                with suppress(Exception):
                    current.close()
                current = renewed

        self._lease_monitor = threading.Thread(
            target=monitor,
            name="fleet-session-lease",
            daemon=True,
        )
        self._lease_monitor.start()

    def close_lease(self) -> None:
        """Stop the lease monitor and close the dedicated lease connection."""
        with self._lease_lock:
            self._lease_monitor_stop.set()
            self.lease_lost.set()
            lease = self.lease
        if self._lease_monitor is not None:
            self._lease_monitor.join(timeout=_LEASE_MONITOR_JOIN_SECONDS)
            self._lease_monitor = None
        if lease is not None:
            lease.close()
            with self._lease_lock:
                self.lease = None

    def set_mode(self, mode: str) -> None:
        """Set the execution mode on the engine and record its Session-level provenance."""
        self.engine.execution_mode = mode
        self.configuration_overrides["execution_mode"] = mode

    def set_engine(self, engine: AdaptiveFleetEngine) -> None:
        """Install a rebuilt engine and point the sandbox manager at its sub LM."""
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
    mode = getattr(getattr(runtime, "engine", None), "execution_mode", settings.execution_mode)
    if mode != settings.execution_mode:
        overrides["execution_mode"] = mode
    return settings.configuration(overrides)


class SessionRuntimeRegistry:
    """Build a separate sandbox, namespace, and engine per Session, once."""

    def __init__(self, settings: FleetSettings, store: Store | None) -> None:
        """Store the settings and optional Store; no Session runtimes exist yet."""
        self.settings = settings
        self.store = store
        self.root_lm: Any = None
        self.sub_lm: Any = None
        self.jev_lm: Any = None
        self._sessions: dict[str, SessionRuntime] = {}
        self._lock = threading.Lock()
        # Serializes first use, eviction, and lease-loss recovery. The fast path only takes `_lock`.
        self._rebuild_lock = threading.RLock()
        self._restart_requested = threading.Event()

    def request_restart(self) -> bool:
        """Fence all runtime admission, reopening it if existing work prevents restart."""
        with self._lock:
            self._restart_requested.set()
            if any(runtime.turn_lock.locked() for runtime in self._sessions.values()):
                self._restart_requested.clear()
                return False
            return True

    def get(self, session_id: str) -> SessionRuntime:
        """Return the Session's runtime, rebuilding it after a lost ownership lease."""
        session_id = validate_session_id(session_id)
        with self._lock:
            if self._restart_requested.is_set():
                raise RuntimeError("Fleet is restarting after setup")
            runtime = self._sessions.get(session_id)
            if runtime is not None and not runtime.lease_lost.is_set():
                return runtime
            if runtime is None:
                self._check_capacity_locked()

        # Only first use and recovery take this path. `_build` runs outside `_lock` so unrelated
        # Sessions keep using their cached runtimes while a new sandbox starts or a stale one shuts
        # down; `_rebuild_lock` serializes every slow path, `evict`, and `close`.
        with self._rebuild_lock:
            with self._lock:
                runtime = self._sessions.get(session_id)
                if runtime is not None and not runtime.lease_lost.is_set():
                    return runtime
                if runtime is None:
                    self._check_capacity_locked()
            if runtime is not None:
                _LOGGER.warning(
                    "Session %s ownership lease was lost; evicting its stale runtime",
                    session_id,
                )
                try:
                    self._evict_locked(session_id)
                except TimeoutError as exc:
                    _LOGGER.warning(
                        "Session %s ownership lease was lost; a Turn is still draining",
                        session_id,
                    )
                    raise RuntimeError(
                        "Session ownership lease was lost while a Turn is still draining; retry when it completes"
                    ) from exc
            with self._lock:
                if self.store is None:
                    raise RuntimeError("Session storage is not configured")
                if self.root_lm is None:
                    self.root_lm, self.sub_lm, _, self.jev_lm = self.settings.build_lms()
            runtime = self._build(session_id)
            with self._lock:
                self._sessions[session_id] = runtime
            return runtime

    def _check_capacity_locked(self) -> None:
        """Refuse a new Session when the registry is full. The caller holds `_lock`."""
        if len(self._sessions) >= self.settings.max_active_sessions:
            raise RuntimeError("The maximum number of active Sessions has been reached")

    def _build(self, session_id: str) -> SessionRuntime:
        """Build a Session runtime, acquire its storage lease, and restore saved settings.

        Starts the lease monitor before returning. Raises RuntimeError when storage is not
        configured; storage and restoration errors propagate, closing an acquired lease
        if restoration or monitor startup fails.
        """
        settings = self.settings
        if self.store is None:
            raise RuntimeError("Session storage is not configured")
        store = self.store
        sandbox_mgr = DaytonaSandboxManager(
            api_key=settings.fleet_daytona_api_key,
            org_id=settings.fleet_daytona_org_id,
            volume_id=settings.fleet_daytona_volume_id,
            shared_notes_subpath=settings.fleet_shared_notes_subpath,
            session_id=session_id,
            auto_stop_minutes=settings.daytona_auto_stop_minutes,
            sub_lm=self.sub_lm,
            jev_lm=self.jev_lm,
            max_llm_calls=settings.max_llm_calls_per_turn,
            max_depth=settings.max_agent_depth,
            max_children=settings.max_child_sandboxes,
            max_active_children=settings.max_active_children,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            linked_children=settings.linked_children,
            environment_report_claim=store.claim_environment_report,
        )
        runtime = SessionRuntime(
            restart_requested=self._restart_requested,
            sandbox_mgr=sandbox_mgr,
            store=store,
            skill_mgr=SkillManager(sandbox_mgr, Path(settings.local_skills_dir)),
            engine=AdaptiveFleetEngine(
                sandbox_mgr=sandbox_mgr,
                root_lm=self.root_lm,
                sub_lm=self.sub_lm,
                jev_lm=self.jev_lm,
                compiled_path=str(Path(settings.compiled_program_dir) / f"{session_id}.json"),
                execution_mode=settings.execution_mode,
            ),
        )
        runtime.ensure_session(session_id)
        runtime.lease = store.acquire_session_lease(session_id)
        try:
            ownership_epoch = store.advance_sandbox_ownership_epoch(session_id)
            if ownership_epoch is None:
                raise RuntimeError(
                    "Session sandbox ownership is unknown; reconcile legacy resources before writable admission"
                )
            runtime.ownership_epoch = ownership_epoch
            ownership = store.sandbox_ownership(session_id)
            if ownership is None:
                raise RuntimeError(
                    "Prior sandbox cleanup is unresolved; reconcile recorded provider resources before admission"
                )
            sandbox_mgr.ownership_epoch = runtime.ownership_epoch

            # Resolve the epoch at call time: a manager that outlives a newer owner must fence,
            # not write under the identity it captured at admission.
            def mutate_sandbox_resource(agent_id: str, resource: dict[str, Any] | None) -> bool:
                epoch = runtime.ownership_epoch
                if epoch is None:
                    raise RuntimeError("Session ownership epoch is unavailable; sandbox write is fenced")
                return store.mutate_sandbox_resource(session_id, epoch, agent_id, resource)

            sandbox_mgr.ownership_resource_mutate = mutate_sandbox_resource
            sandbox_mgr.reconcile_sandbox_ownership(ownership["resources"])
            store.fail_interrupted_turns(session_id, ownership_epoch)
            saved = store.runtime_settings(session_id)
            if "mode" in saved:
                runtime.set_mode(settings.for_session(saved).execution_mode)
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
        """Build an engine for this Session that uses the given root and sub model names."""
        settings = self.settings.for_session({"models": models})
        root, sub, _, _ = settings.build_lms()
        return AdaptiveFleetEngine(
            sandbox_mgr=runtime.sandbox_mgr,
            root_lm=root,
            sub_lm=sub,
            jev_lm=self.jev_lm,
            compiled_path=runtime.engine.compiled_path,
            execution_mode=runtime.engine.execution_mode,
        )

    def evict(self, session_id: str) -> bool:
        """Stop one Session's sandbox and release its lease; False when it has no runtime in this process.

        Stored history is untouched. Unconfirmed cleanup retains the fenced runtime and lease
        and raises RuntimeError; retry eviction to reconcile before rebuilding.
        """
        session_id = validate_session_id(session_id)
        with self._rebuild_lock:
            return self._evict_locked(session_id)

    def _evict_locked(self, session_id: str) -> bool:
        """Evict one runtime. The caller must hold `_rebuild_lock`."""
        with self._lock:
            runtime = self._sessions.get(session_id)
        if runtime is None:
            return False
        if not runtime.turn_lock.acquire(timeout=_EVICT_WAIT_SECONDS):
            raise TimeoutError("A Turn is running on this Session")
        try:
            with self._lock:
                if self._sessions.get(session_id) is not runtime:
                    return False
                runtime.lease_lost.set()
            if runtime.sandbox_mgr.shutdown() is not True:
                raise RuntimeError("Session sandbox cleanup is unconfirmed; runtime retained for cleanup retry")
            runtime.close_lease()
            with self._lock:
                self._sessions.pop(session_id, None)
        finally:
            runtime.turn_lock.release()
        return True

    def close(self) -> None:
        """Attempt all shutdowns, retaining fenced runtimes/leases whose cleanup is unconfirmed."""
        with self._rebuild_lock:
            with self._lock:
                runtimes = tuple(self._sessions.values())
                for runtime in runtimes:
                    runtime.lease_lost.set()

            def shutdown(runtime: SessionRuntime) -> bool:
                try:
                    return runtime.sandbox_mgr.shutdown() is True
                except Exception:
                    _LOGGER.warning("Session shutdown failed; retaining unresolved runtime")
                    return False

            unresolved = False
            if runtimes:
                with ThreadPoolExecutor(max_workers=min(8, len(runtimes))) as pool:
                    confirmations = list(pool.map(shutdown, runtimes))
                for runtime, confirmed in zip(runtimes, confirmations, strict=True):
                    if confirmed:
                        try:
                            runtime.close_lease()
                        except Exception:
                            unresolved = True
                            _LOGGER.warning("Session lease release failed; retaining fenced runtime")
                            continue
                        with self._lock:
                            self._sessions.pop(runtime.sandbox_mgr.session_id, None)
                    else:
                        unresolved = True
            if unresolved:
                raise RuntimeError("Session sandbox cleanup is unconfirmed; runtimes retained for cleanup retry")
