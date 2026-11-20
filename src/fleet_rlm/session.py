"""Per-Session runtime: sandbox, durable state policies, Skills, and engine, built on first use."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext, suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import RequiredSkill, SkillAdmissionError
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.reasoning import deserialize_rlm_history
from fleet_rlm.models import build_lm
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.skills import SkillManager
from fleet_rlm.store import IdempotencyConflictError, PooledLease, Store
from fleet_rlm.workspace import WorkspaceDataset, _workspace_relative_path, validate_session_id

_LOGGER = logging.getLogger("fleet_rlm.session")
_EVICT_WAIT_SECONDS = 2.0
_IDLE_EVICT_SECONDS = 600.0


def memory_search_query(query: str) -> str:
    """Bound the memory query before constructing PostgreSQL OR terms."""
    return " OR ".join(query[:2000].split()[:32])


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Loaded Session context passed into Turn input preparation."""

    memory_summary: str
    session_history: str
    rlm_history: Any = None


_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000


def _required_intent_digest(task: str, names: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(["fleet-turn-intent-v2", task, list(names)], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(slots=True)
class SessionRuntime:
    """All mutable runtime resources for one private Session.

    `turn_lock` serializes Turns and workspace changes. It is a threading lock: routes run in
    FastAPI's threadpool, and the streaming Turn route acquires it from a worker thread.
    """

    sandbox_mgr: DaytonaSandboxManager
    store: Store
    skill_mgr: SkillManager
    engine: FleetRuntime
    turn_lock: threading.Lock = field(default_factory=threading.Lock)
    context: WorkspaceDataset | None = None
    workspace_state: str = "unknown"
    lease: PooledLease | None = None
    ownership_epoch: int | None = None
    configuration_overrides: dict[str, Any] = field(default_factory=dict)
    lease_lost: threading.Event = field(default_factory=threading.Event, repr=False)
    last_active_at: float = field(default_factory=time.monotonic, repr=False)
    _lease_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    storage_settings: FleetSettings | None = None

    def touch(self) -> None:
        """Record recent Session runtime activity for idle eviction."""
        self.last_active_at = time.monotonic()

    @property
    def session_id(self) -> str:
        """Return the Session ID owned by the sandbox manager."""
        return self.sandbox_mgr.session_id

    def set_workspace_state(self, value: str) -> None:
        """Persist whether this Session's workspace is still known to be empty."""
        if value not in {"known_empty", "unknown"}:
            raise ValueError("Invalid workspace state")
        self.assert_lease_held()
        if self.workspace_state != value:
            self.store.save_runtime_settings(self.session_id, {"workspace_state": value})
            self.workspace_state = value

    def select_context(self, session_id: str, path: str, description: str) -> WorkspaceDataset:
        """Select a workspace dataset by metadata while the caller holds the Session lock."""
        metadata = self.sandbox_mgr.stat_workspace_file(path)
        context = WorkspaceDataset(
            workspace_path=path,
            description=description,
            size_bytes=metadata["size_bytes"],
            sha256=metadata["sha256"],
        )
        self._save_context(session_id, context)
        return context

    def clear_context(self, session_id: str) -> None:
        """Clear selected context while retaining the current workspace state."""
        self._save_context(session_id, None)

    def _save_context(self, session_id: str, context: WorkspaceDataset | None) -> None:
        saved = {
            "mode": self.engine.execution_mode,
            "context": context.to_descriptor() if context is not None else None,
        }
        if context is not None:
            saved["workspace_state"] = "unknown"
        self.store.save_runtime_settings(session_id, saved)
        if context is not None:
            self.workspace_state = "unknown"
        self.context = context

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
        search = memory_search_query(query)
        return self.store.memories(self.session_id, search, reviewed_only=reviewed_only)

    def delete_memory(self, memory_id: str) -> None:
        """Delete one memory after validating its ID."""
        self.store.delete_memory(self.session_id, validate_session_id(memory_id))

    def completed_turn(self, key: str, task: str, requested_skills: tuple[str, ...]) -> dict[str, Any] | None:
        """Recover required-intent success before reading today's deployment sources."""
        prior = self.store.turn_by_key(self.session_id, key)
        if prior is None or prior["status"] != "completed":
            return None
        if not prior["task_sha256"].startswith(f"v2:{_required_intent_digest(task, requested_skills)}:"):
            raise IdempotencyConflictError("Idempotency key already belongs to another task")
        return prior

    def claim_turn(
        self,
        turn_id: str,
        key: str,
        task: str,
        *,
        requested_skills: tuple[str, ...] = (),
        required_skills: tuple[RequiredSkill, ...] = (),
        admission_error: SkillAdmissionError | None = None,
        submission: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist intent before side effects; an uncertain running turn is never replayed."""
        intent_task = task
        if submission:
            intent_task = json.dumps({"task": task, "submission": submission}, sort_keys=True)
        digest = hashlib.sha256(intent_task.encode()).hexdigest()
        intent = None
        if requested_skills:
            intent = _required_intent_digest(intent_task, requested_skills)
            identities = [[skill.name, skill.source, skill.sha256] for skill in required_skills]
            source = (
                "refused:admission"
                if admission_error is not None
                else hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()
            )
            digest = f"v2:{intent}:{source}"
        ownership = {"ownership_epoch": self.ownership_epoch} if self.ownership_epoch is not None else {}
        prior = self.store.claim_turn(
            self.session_id,
            turn_id,
            key,
            digest,
            task=task,
            **({"submission": submission} if submission else {}),
            **ownership,
        )
        if prior is not None and prior["task_sha256"] != digest:
            # A completed request is a receipt, not permission to execute today's deployment.
            if (
                intent is not None
                and prior["status"] == "completed"
                and prior["task_sha256"].startswith(f"v2:{intent}:")
            ):
                return prior
            raise IdempotencyConflictError("Idempotency key already belongs to another task")
        return prior

    def load_context(self, session_id: str) -> SessionContext:
        """Load the Session's memory summary, bounded rendered history, and deserialized RLM history."""
        session_id = validate_session_id(session_id)
        summary, turns = self.store.load_session(session_id)
        return SessionContext(
            memory_summary=summary,
            session_history=self._render_history(turns),
            rlm_history=deserialize_rlm_history(turns),
        )

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
        commit_guard: AbstractContextManager[None] | None = None,
    ) -> dict[str, Any]:
        """Commit and return the canonical completion, including the current artifact inventory.

        Commits the record, memory note, completion payload, and optional immutable replay
        together. `replay_snapshot` is the digest and compressed bytes selected by the record.
        The Turn record carries the validated artifact inventory.
        `commit_guard` arbitrates cancellation around persistence after remote publication.

        Raises:
            ValueError: Replay selection metadata or digest is inconsistent, the Turn ID is
                missing, the Session ID is invalid, the record is oversized, or the captured
                artifact inventory is invalid.

        The returned payload owns its nested values independently of the record and Store.
        Serialization and storage errors propagate.
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
        capture = turn_record.get("artifact_capture")
        if not isinstance(capture, dict) or not isinstance(capture.get("complete"), bool):
            raise ValueError("Turn record requires an artifact capture")
        artifacts = capture.get("paths")
        if not isinstance(artifacts, list) or any(
            not isinstance(path, str) or not path.startswith("artifacts/") or not self._valid_vfs_path(path)
            for path in artifacts
        ):
            raise ValueError("Artifact capture contains an invalid relative path")
        if artifacts != sorted(set(artifacts)):
            raise ValueError("Artifact capture paths must be sorted and unique")
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
                for key in (
                    "lm_calls",
                    "sandbox_errors",
                    "submit_rejections",
                    "budget_breakdown",
                    # Direct mode inlines the workspace; the caller needs that measurement too.
                    "direct_context_chars",
                    "direct_context_truncated",
                    "replay_snapshot_selected",
                    "requested_skills",
                    "required_skills",
                )
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
        result["assistant_message_id"] = f"{turn_record['turn_id']}_assistant"
        published_files = []
        # The runtime registry installs host storage configuration; no credentials enter Daytona.
        storage_settings = self.storage_settings
        if storage_settings is not None and storage_settings.fleet_storage_bucket:
            import tempfile
            from pathlib import Path

            from fleet_rlm.object_storage import ObjectStorage

            storage = ObjectStorage(storage_settings)
            changes = turn_record.get("artifact_changes", {}).get("files", {})
            for artifact in artifacts:
                if changes.get(artifact, {}).get("state") not in {"created", "modified"}:
                    continue
                with tempfile.TemporaryDirectory(prefix="fleet-artifact-") as temporary:
                    path = Path(temporary) / "content"
                    self.sandbox_mgr.read_workspace_file_to_path(artifact, str(path), max_bytes=25_000_000)
                    published = storage.upload(
                        session_id,
                        path,
                        filename=Path(artifact).name,
                        origin="artifact",
                        turn_id=turn_record["turn_id"],
                        workspace_path=artifact,
                    )
                    if published["sha256"] != changes[artifact].get("sha256"):
                        raise RuntimeError("Artifact changed after its verified evidence capture")
                    self.sandbox_mgr.check_turn()
                    published_files.append(published)
            self.sandbox_mgr.check_turn()
            result["files"] = [
                {k: v for k, v in file.items() if k not in {"bucket", "object_key"}} for file in published_files
            ]
        completion = (turn_record["turn_id"], deepcopy(result))
        with commit_guard if commit_guard is not None else nullcontext():
            self.store.commit_turn(
                session_id,
                turn_record_json=record_json,
                memory_note=new_memory_note,
                completion=completion,
                replay_snapshot=replay_snapshot,
                **({"published_files": published_files} if published_files else {}),
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
        """Fail closed after the pooled Session ownership lease is lost or expired."""
        if self.lease_lost.is_set():
            raise RuntimeError("Session ownership lease was lost; runtime must be rebuilt")

    def start_lease_monitor(self) -> None:
        """Register this Session's pooled lease with Store's shared heartbeat daemon."""
        lease = self.lease
        assert lease is not None

        def on_lost() -> None:
            self.lease_lost.set()
            with suppress(Exception):
                self.sandbox_mgr.request_cancellation()

        self.store.heartbeat.register_session(
            self.sandbox_mgr.session_id,
            lease.epoch,
            owner_id=lease.owner_id,
            on_lost=on_lost,
        )

    def close_lease(self) -> None:
        """Unregister heartbeat renewal and release the pooled Session lease."""
        with self._lease_lock:
            self.lease_lost.set()
            lease = self.lease
        if lease is not None:
            lease.close()
            with self._lease_lock:
                if self.lease is lease:
                    self.lease = None

    def set_mode(self, mode: str) -> None:
        """Set the execution mode on the engine and record its Session-level provenance."""
        self.engine.execution_mode = mode
        self.configuration_overrides["execution_mode"] = mode

    def set_engine(self, engine: FleetRuntime) -> None:
        """Install a rebuilt engine and point the sandbox manager at its sub LM."""
        self.engine = engine
        self.sandbox_mgr.sub_lm = engine.sub_lm
        self.configuration_overrides.update(
            {
                "fleet_root_model": engine.root_lm.model,
                "fleet_sub_model": engine.sub_lm.model,
            }
        )


def runtime_configuration(
    settings: FleetSettings,
    runtime: Any | None = None,
    *,
    effective_settings: FleetSettings | None = None,
) -> dict[str, Any]:
    """The resolved configuration with this Session's model and mode overrides applied."""
    if runtime is None:
        effective = effective_settings if effective_settings is not None else settings
        overrides: dict[str, Any] = {}
        for role in ("root", "sub"):
            model = effective.resolved_model(role).model
            if model != settings.resolved_model(role).model:
                overrides[f"fleet_{role}_model"] = model
        if effective.execution_mode != settings.execution_mode:
            overrides["execution_mode"] = effective.execution_mode
        return settings.configuration(overrides)
    overrides = dict(getattr(runtime, "configuration_overrides", {}))
    for role in ("root", "sub"):
        model = getattr(getattr(runtime.engine, f"{role}_lm", None), "model", None)
        if model is not None and model != settings.resolved_model(role).model:
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
        self._session_gates: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
        self._building: set[str] = set()
        self._lock = threading.Lock()

    def _session_gate(self, session_id: str) -> threading.RLock:
        """Return the per-Session lock serializing build, lease recovery, and eviction."""
        with self._lock:
            return self._session_gates.setdefault(session_id, threading.RLock())

    def peek(self, session_id: str) -> SessionRuntime | None:
        """Return an active cached SessionRuntime without acquiring or building a new one."""
        session_id = validate_session_id(session_id)
        with self._lock:
            runtime = self._sessions.get(session_id)
            if runtime is not None and not runtime.lease_lost.is_set():
                return runtime
            return None

    def get(self, session_id: str) -> SessionRuntime:
        """Return the Session's runtime, rebuilding it after a lost ownership lease."""
        session_id = validate_session_id(session_id)
        with self._lock:
            runtime = self._sessions.get(session_id)
            if runtime is not None and not runtime.lease_lost.is_set():
                runtime.touch()
                return runtime
            if runtime is None and session_id not in self._building:
                self._check_capacity_locked()

        # Only first use and recovery take this path. Per-Session gates let independent
        # Sessions initialize and shut down in parallel without holding `_lock`.
        with self._session_gate(session_id):
            with self._lock:
                runtime = self._sessions.get(session_id)
                if runtime is not None and not runtime.lease_lost.is_set():
                    runtime.touch()
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
                    self.root_lm = build_lm(self.settings, self.settings.fleet_root_model, "root")
                    self.sub_lm = build_lm(self.settings, self.settings.fleet_sub_model, "sub")
                    self.jev_lm = self.settings.build_jev_lm()
            with self._lock:
                self._check_capacity_locked()
                self._building.add(session_id)
            try:
                runtime = self._build(session_id)
                runtime.touch()
                with self._lock:
                    self._sessions[session_id] = runtime
                return runtime
            finally:
                with self._lock:
                    self._building.discard(session_id)

    def _check_capacity_locked(self) -> None:
        """Refuse a new Session when the registry is full. The caller holds `_lock`."""
        if len(self._sessions) + len(self._building) >= self.settings.max_active_sessions:
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
        compiled_path = Path(settings.compiled_program_dir) / "fleet.json"

        lease = store.acquire_session_lease(session_id)
        try:
            saved = store.runtime_settings(session_id)
            effective = settings.for_session(saved)
            root_lm, sub_lm = self.root_lm, self.sub_lm
            if saved.get("models"):
                root_lm = build_lm(effective, effective.fleet_root_model, "root")
                sub_lm = build_lm(effective, effective.fleet_sub_model, "sub")
            sandbox_mgr = DaytonaSandboxManager(
                api_key=settings.fleet_daytona_api_key,
                org_id=settings.fleet_daytona_org_id,
                volume_id=settings.fleet_daytona_volume_id,
                shared_notes_subpath=settings.fleet_shared_notes_subpath,
                session_id=session_id,
                auto_stop_minutes=settings.daytona_auto_stop_minutes,
                sub_lm=sub_lm,
                jev_lm=self.jev_lm,
                serper_api_key=settings.serper_api_key.get_secret_value(),
                you_api_key=settings.you_api_key.get_secret_value(),
                max_llm_calls=settings.max_llm_calls_per_turn,
                max_depth=settings.max_agent_depth,
                max_children=settings.max_child_sandboxes,
                max_active_children=settings.max_active_children,
                turn_timeout_seconds=settings.turn_timeout_seconds,
                linked_children=settings.linked_children,
                environment_report_claim=store.claim_environment_report,
            )
            runtime = SessionRuntime(
                sandbox_mgr=sandbox_mgr,
                store=store,
                skill_mgr=SkillManager(sandbox_mgr, Path(settings.local_skills_dir)),
                engine=FleetRuntime(
                    sandbox_mgr=sandbox_mgr,
                    root_lm=root_lm,
                    sub_lm=sub_lm,
                    jev_lm=self.jev_lm,
                    compiled_path=str(compiled_path),
                    baseline=not (compiled_path.exists() or compiled_path.is_symlink()),
                    installed_program=store.approved_program,
                    execution_mode=effective.execution_mode,
                    root_adapter=effective.resolved_model("root").build_adapter(),
                    sub_adapter=effective.resolved_model("sub").build_adapter(),
                ),
                lease=lease,
                ownership_epoch=lease.epoch,
                storage_settings=settings,
            )
            if lease.created:
                # The insert does not list the Volume. A new schema must not reuse a Volume
                # that already holds this Session's subtree; see ARCHITECTURE.md.
                saved["workspace_state"] = "known_empty"
                store.save_runtime_settings(session_id, {"workspace_state": "known_empty"})
            runtime.workspace_state = saved.get("workspace_state", "unknown")
            if runtime.workspace_state not in {"known_empty", "unknown"}:
                runtime.workspace_state = "unknown"
            ownership = store.sandbox_ownership(session_id)
            if ownership is None:
                raise RuntimeError(
                    "Prior sandbox cleanup is unresolved; reconcile recorded provider resources before admission"
                )
            sandbox_mgr.ownership_epoch = runtime.ownership_epoch
            if ownership["resources"] and runtime.workspace_state == "known_empty":
                runtime.set_workspace_state("unknown")

            # Resolve the epoch at call time: a manager that outlives a newer owner must fence,
            # not write under the identity it captured at admission.
            def mutate_sandbox_resource(agent_id: str, resource: dict[str, Any] | None) -> bool:
                epoch = runtime.ownership_epoch
                if epoch is None:
                    raise RuntimeError("Session ownership epoch is unavailable; sandbox write is fenced")
                return store.mutate_sandbox_resource(session_id, epoch, agent_id, resource)

            sandbox_mgr.ownership_resource_mutate = mutate_sandbox_resource
            sandbox_mgr.reconcile_sandbox_ownership(ownership["resources"])
            store.fail_interrupted_turns(session_id, lease.epoch)
            runtime.engine.reload_program()
            if "mode" in saved:
                runtime.configuration_overrides["execution_mode"] = effective.execution_mode
            if saved.get("models"):
                runtime.configuration_overrides.update(
                    fleet_root_model=root_lm.model,
                    fleet_sub_model=sub_lm.model,
                )
            if saved.get("context"):
                runtime.context = WorkspaceDataset(**saved["context"])
            runtime.start_lease_monitor()
        except BaseException:
            lease.close()
            raise
        return runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> FleetRuntime:
        """Build an engine for this Session that uses the given root and sub model names."""
        settings = self.settings.for_session({"models": models})
        root = build_lm(settings, settings.fleet_root_model, "root")
        sub = build_lm(settings, settings.fleet_sub_model, "sub")
        return FleetRuntime(
            sandbox_mgr=runtime.sandbox_mgr,
            root_lm=root,
            sub_lm=sub,
            jev_lm=self.jev_lm,
            compiled_path=runtime.engine.compiled_path,
            baseline=not runtime.engine.compiled.digest,
            installed_program=runtime.engine.installed_program,
            execution_mode=runtime.engine.execution_mode,
            root_adapter=settings.resolved_model("root").build_adapter(),
            sub_adapter=settings.resolved_model("sub").build_adapter(),
        )

    def evict(self, session_id: str) -> bool:
        """Stop one Session's sandbox and release its lease; False when it has no runtime in this process.

        Stored history is untouched. Unconfirmed cleanup retains the fenced runtime and lease
        and raises RuntimeError; retry eviction to reconcile before rebuilding.
        """
        session_id = validate_session_id(session_id)
        with self._session_gate(session_id):
            return self._evict_locked(session_id)

    def evict_idle(self, *, max_idle_seconds: float = _IDLE_EVICT_SECONDS) -> int:
        """Evict cached runtimes whose idle time meets `max_idle_seconds` and hold no active Turn."""
        now = time.monotonic()
        with self._lock:
            candidates = [
                session_id
                for session_id, runtime in self._sessions.items()
                if now - runtime.last_active_at >= max_idle_seconds and not runtime.turn_lock.locked()
            ]
        evicted = 0
        for session_id in candidates:
            gate = self._session_gate(session_id)
            if not gate.acquire(blocking=False):
                continue
            try:
                with self._lock:
                    runtime = self._sessions.get(session_id)
                    if (
                        runtime is None
                        or time.monotonic() - runtime.last_active_at < max_idle_seconds
                        or runtime.turn_lock.locked()
                    ):
                        continue
                if self._evict_locked(session_id):
                    evicted += 1
            except (TimeoutError, RuntimeError):
                _LOGGER.warning("Idle eviction skipped for Session %s", session_id)
            finally:
                gate.release()
        return evicted

    def _evict_locked(self, session_id: str) -> bool:
        """Evict one runtime. The caller must hold `_session_gate(session_id)`."""
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
        with self._lock:
            items = tuple(self._sessions.items())
            for _sid, runtime in items:
                runtime.lease_lost.set()

        def shutdown(item: tuple[str, SessionRuntime]) -> bool:
            session_id, runtime = item
            with self._session_gate(session_id):
                try:
                    return runtime.sandbox_mgr.shutdown() is True
                except Exception:
                    _LOGGER.warning("Session shutdown failed; retaining unresolved runtime")
                    return False

        unresolved = False
        if items:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
                confirmations = list(pool.map(shutdown, items))
            for (session_id, runtime), confirmed in zip(items, confirmations, strict=True):
                if confirmed:
                    try:
                        with self._session_gate(session_id):
                            runtime.close_lease()
                    except Exception:
                        unresolved = True
                        _LOGGER.warning("Session lease release failed; retaining fenced runtime")
                        continue
                    with self._lock:
                        if self._sessions.get(session_id) is runtime:
                            self._sessions.pop(session_id, None)
                else:
                    unresolved = True
        if unresolved:
            raise RuntimeError("Session sandbox cleanup is unconfirmed; runtimes retained for cleanup retry")


def update_runtime_preferences(
    runtime: SessionRuntime,
    registry: SessionRuntimeRegistry,
    session_id: str,
    *,
    mode: str | None = None,
    model: str | None = None,
    role: str = "root",
    persist_context: bool = False,
) -> None:
    """Build, save, and apply preferences while the caller holds the Session lock."""
    saved: dict[str, Any] = {}
    engine = None
    if model is not None:
        models = {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model, role: model}
        engine = registry.engine_for_models(runtime, models)
        saved["models"] = models
    if mode is not None:
        saved["mode"] = mode
        if persist_context:
            saved["context"] = runtime.context.to_descriptor() if runtime.context else None
    runtime.store.save_runtime_settings(session_id, saved)
    if engine is not None:
        runtime.set_engine(engine)
    if mode is not None:
        runtime.set_mode(mode)
