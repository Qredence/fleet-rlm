"""Sandbox provisioning, lease-fenced ownership, child allocation, and teardown for DaytonaSandboxManager."""

from __future__ import annotations

import functools
import hashlib
import threading
from typing import TYPE_CHECKING, Any

import mlflow
from daytona import CreateSandboxFromSnapshotParams, ListSandboxesQuery, VolumeMount

from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.snapshots import SnapshotIdentity
from fleet_rlm.workspace import _child_input_path, _workspace_relative_path, session_volume_subpath

if TYPE_CHECKING:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

CLEAR_WORKSPACE_CODE = (
    "import os,shutil\n"
    "for name in os.listdir('/workspace'):\n"
    " p=os.path.join('/workspace',name)\n"
    " try:\n"
    "  if os.path.islink(p) or not os.path.isdir(p): os.unlink(p)\n"
    "  else: shutil.rmtree(p)\n"
    " except FileNotFoundError: pass\n"
)

# Daytona sandbox states, as the provider reports them after `refresh_data()`.
_RESUMABLE_STATES = frozenset({"stopped", "archived", "paused"})
_SETTLING_STATES = frozenset({"stopping", "archiving", "pausing", "starting", "resuming", "restoring", "creating"})
_FAILED_STATES = frozenset({"error", "build_failed", "destroyed", "destroying"})
# Provider lifecycle bounds are requested, never removed: a deadline-free Turn still has them.
_ROOT_TTL_MINUTES = 120
_CHILD_TTL_MINUTES = 30


class SandboxOwnershipLostError(RuntimeError):
    """A newer Session epoch owns reconciliation; stale managers must not mutate it."""


def _provider_minutes(value: Any) -> int | None:
    """Return a provider-reported minute bound as an integer, or None when it is absent."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _sandbox_state(sandbox: Any) -> str:
    """Refresh the handle from the provider and return its lowercase state ('' when unknown)."""
    refresh = getattr(sandbox, "refresh_data", None)
    if callable(refresh):
        refresh()
    state = getattr(sandbox, "state", None)
    return str(getattr(state, "value", state) or "").lower()


class SandboxLifecycleMixin:
    """Mixin owning sandbox creation, resumption, ownership leases, children, and teardown."""

    __slots__ = ()

    @property
    def snapshot_identity(self: DaytonaSandboxManager) -> SnapshotIdentity | None:
        """Return the provider identity resolved by the Turn preflight, if any."""
        return self._snapshot_identity

    def snapshot_provenance(self: DaytonaSandboxManager) -> dict[str, Any]:
        """Return the preflight-resolved identity as flat provenance fields."""
        identity = self._snapshot_identity
        return identity.provenance() if identity is not None else {}

    def provider_bounds(self: DaytonaSandboxManager) -> dict[str, Any]:
        """Report the provider-owned lifecycle bounds without changing or hiding them."""
        root = self.root
        sandbox = root._sandbox
        requested_ttl = _CHILD_TTL_MINUTES if self.parent is not None else _ROOT_TTL_MINUTES
        ttl = _provider_minutes(getattr(sandbox, "ttl_minutes", None))
        auto_stop = _provider_minutes(getattr(sandbox, "auto_stop_interval", None))
        lifespan_value = getattr(sandbox, "maximum_lifespan_minutes", None)
        if lifespan_value is None:
            lifespan_value = getattr(sandbox, "max_lifespan_minutes", None)
        if lifespan_value is None:
            lifespan_value = getattr(sandbox, "lifespan_minutes", None)
        lifespan = _provider_minutes(lifespan_value)
        return {
            "ttl_minutes": requested_ttl if ttl is None else ttl,
            "auto_stop_interval": root.auto_stop_minutes if auto_stop is None else auto_stop,
            "maximum_lifespan_minutes": lifespan,
            "provider_owned": True,
        }

    @property
    def live_sandbox_id(self: DaytonaSandboxManager) -> str | None:
        """Return the live root provider handle id, or None when nothing was provisioned."""
        sandbox = self.root._sandbox
        return getattr(sandbox, "id", None) if sandbox is not None else None

    def allocate_child(self: DaytonaSandboxManager, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        """Trace preparation separately from generated execution, without recording file contents."""
        with self._lifecycle_span("sandbox.child_prepare") as span:
            child = self._allocate_child(input_paths=input_paths)
            span.set_attribute("child_agent_id", child.agent_id)
            span.set_attribute("child_sandbox_id", getattr(child._sandbox, "id", None))
            return child

    def _allocate_child(self: DaytonaSandboxManager, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        """Reserve and provision a child sandbox, copying the requested or default inputs into it."""
        from fleet_rlm.sandbox import manager as _mgr

        root = self.root
        self.check_turn()
        self._refuse_unresolved_cleanup()
        selected_paths: list[str] | None = None
        ambient_skills: set[str] = set()
        if input_paths is not None:
            if not isinstance(input_paths, list):
                raise TypeError("input_paths must be a list of workspace paths")
            if len(input_paths) > 100:
                raise ValueError("Child input file limit exceeded")
            selected_paths = [_child_input_path(path) for path in input_paths]
            available_paths = set()
            for path in self.list_workspace_files():
                try:
                    available_paths.add(_workspace_relative_path(path))
                except ValueError:
                    continue
            missing_paths = [path for path in selected_paths if path not in available_paths]
            if missing_paths:
                raise ValueError(f"Child input file is unavailable: {missing_paths[0]}")
            ambient_skills = {path for path in available_paths if path.startswith("skills/")} - set(selected_paths)
            selected_paths.extend(sorted(ambient_skills))

        with root._budget_lock:
            if self.depth >= root.max_depth:
                raise RuntimeError("Agent recursion depth exhausted")
            if root._allocations >= root.max_children or len(root._children) >= root.max_active_children:
                raise RuntimeError("Child sandbox capacity exhausted; spawn refused to avoid a wait cycle")
            child = _mgr.DaytonaSandboxManager(
                self.api_key,
                self.org_id,
                self.volume_id,
                self.session_id,
                snapshot=self.snapshot,
                auto_stop_minutes=self.auto_stop_minutes,
                sub_lm=self.sub_lm,
                sub_adapter=self.sub_adapter,
                jev_lm=self.jev_lm,
                max_llm_calls=root.max_llm_calls,
                parent=self,
                linked_children=root.linked_children,
                ownership_epoch=root.ownership_epoch,
                ownership_resource_mutate=root.ownership_resource_mutate,
            )
            root._children[child.agent_id] = child
            root._allocations += 1
        child._deadline = root._deadline
        child._deadline_source = root._deadline_source
        child._effective_deadline_seconds = root._effective_deadline_seconds
        child._turn_generation = root._turn_generation
        child._turn_active = root._turn_active
        spawn_inputs = [path for path in selected_paths if path not in ambient_skills] if selected_paths else []
        child.emit("agent_spawn", depth=child.depth, **({"input_paths": spawn_inputs} if spawn_inputs else {}))
        with root._budget_lock:
            root._child_receipts[child.agent_id] = {
                "agent_id": child.agent_id,
                "parent_agent_id": self.agent_id,
                "inputs": {},
                "artifacts": [],
                "status": "running",
            }
        try:
            child.ensure_sandbox()
            with child._lifecycle_span("sandbox.input_copy") as span:
                total = 0
                copied = 0
                try:
                    paths_to_copy = selected_paths
                    if paths_to_copy is None:
                        paths_to_copy = [
                            path for path in self.list_workspace_files() if path.startswith(("files/", "skills/"))
                        ]
                    with self._lifecycle_span("sandbox.input_read") as read_span:
                        read_span.set_attribute("target_agent_id", child.agent_id)
                        read_span.set_attribute("files_requested", len(paths_to_copy))
                        try:
                            contents = self.read_workspace_files(paths_to_copy)
                        except ValueError as exc:
                            if "transfer quota" in str(exc):
                                raise ValueError("Child input transfer quota exceeded") from exc
                            raise
                        total = sum(len(data) for data in contents.values())
                        read_span.set_attributes({"files_read": len(contents), "bytes_read": total})
                    with child._lifecycle_span("sandbox.input_write") as write_span:
                        write_span.set_attributes(
                            {
                                "source_agent_id": self.agent_id,
                                "files_requested": len(contents),
                                "bytes_requested": total,
                            }
                        )
                        child.write_workspace_files(contents)
                    copied = len(contents)
                    with root._budget_lock:
                        root._child_receipts[child.agent_id]["inputs"].update(
                            {
                                path: hashlib.sha256(data).hexdigest()
                                for path, data in contents.items()
                                if path not in ambient_skills
                            }
                        )
                finally:
                    span.set_attributes({"files_copied": copied, "bytes_read": total})
            return child
        except BaseException:
            child.record_child_outcome(False)
            child.shutdown()
            raise

    def record_child_outcome(self: DaytonaSandboxManager, succeeded: bool) -> None:
        """Record invocation success independently of transport/tool-call success."""
        if self.parent is not None:
            with self.root._budget_lock:
                receipt = self.root._child_receipts.get(self.agent_id)
                if receipt is not None:
                    receipt["status"] = "succeeded" if succeeded else "failed"

    @property
    def cleanup_outcome(self: DaytonaSandboxManager) -> str | None:
        """The teardown outcome recorded for the current Turn, or None before any teardown."""
        return self.root._cleanup_outcome

    def request_cancellation(self: DaytonaSandboxManager) -> bool:
        """Claim this Turn's single terminal cancellation and run it, or wait for its owner."""
        root = self.root
        with root._teardown_lock:
            claimed = not root._cancellation_claimed
            root._cancellation_claimed = True
        if not claimed:
            root._teardown_done.wait()
            return False
        try:
            self.cancel_turn()
        finally:
            root._teardown_done.set()
        return True

    def cancel_turn(self: DaytonaSandboxManager) -> None:
        """Cancel the Turn, stop child sandboxes, and stop the root sandbox to interrupt remote Python."""
        from fleet_rlm.sandbox import manager as _mgr

        root = self.root
        root._cancelled.set()
        root._fence_executions()
        with root._teardown_lock:
            root._cancellation_claimed = True
            if root._cleanup_outcome is not None:
                return
            try:
                root._notify("cancellation_requested")
                with root._shutdown_lock:
                    confirmed = root._shutdown_locked()
                root._cleanup_outcome = _mgr.CLEANUP_STOPPED if confirmed else _mgr.CLEANUP_UNCONFIRMED
            finally:
                root._teardown_done.set()

    def volume_mounts(self: DaytonaSandboxManager, *, child: bool) -> list[VolumeMount]:
        """Session subtree at /workspace; a root sandbox also gets the shared notes subtree at /learned."""
        mounts = [
            VolumeMount(
                volume_id=self.volume_id,
                mount_path="/workspace",
                subpath=f"agents/{self.session_id}/{self.agent_id}"
                if child
                else session_volume_subpath(self.session_id),
            )
        ]
        if not child and self.shared_notes_subpath:
            mounts.append(
                VolumeMount(volume_id=self.volume_id, mount_path="/learned", subpath=self.shared_notes_subpath)
            )
        return mounts

    def ensure_sandbox(self: DaytonaSandboxManager) -> Any:
        """Return the live handle, tracing provisioning only for create/resume."""
        if self._sandbox is not None:
            return self._ensure_sandbox()
        with self._lifecycle_span("sandbox.provision") as span:
            span.set_attribute("action", "create")
            return self._ensure_sandbox()

    def resource_sample(self: DaytonaSandboxManager) -> dict[str, float | int] | None:
        """Latest CPU, memory, and disk usage of the live sandbox, or None when unavailable."""
        from fleet_rlm.sandbox import manager as _mgr

        with self._lock:
            sandbox = self._sandbox
            stalled = self._resource_sampler
            if sandbox is None or (stalled is not None and stalled.is_alive()):
                return None
            outcome: list[Any] = []

            def sample() -> None:
                try:
                    metrics = sandbox.get_metrics_latest()
                    outcome.append(
                        {
                            "sandbox.cpu_used_pct": metrics.cpu_used_pct,
                            "sandbox.mem_used_bytes": metrics.mem_used,
                            "sandbox.mem_total_bytes": metrics.mem_total,
                            "sandbox.disk_used_bytes": metrics.disk_used,
                        }
                    )
                except Exception as exc:
                    _mgr._LOGGER.debug("Sandbox resource sample unavailable (%s)", type(exc).__name__)

            sampler = threading.Thread(target=sample, name="sandbox-resource-sample", daemon=True)
            self._resource_sampler = sampler
            sampler.start()
        sampler.join(_mgr.RESOURCE_SAMPLE_TIMEOUT_SECONDS)
        return outcome[0] if outcome else None

    def _refuse_unresolved_cleanup(self: DaytonaSandboxManager) -> None:
        """Deny writable use while this agent or its root still awaits cleanup confirmation."""
        if self._ownership_lost or self.root._ownership_lost:
            raise SandboxOwnershipLostError("Session ownership epoch changed; sandbox operation is fenced")
        if self._cleanup_pending or self.root._cleanup_pending:
            raise RuntimeError("Session sandbox cleanup is unconfirmed; retry shutdown before writable reuse")

    def _ensure_sandbox(self: DaytonaSandboxManager) -> Any:
        """Create or resume this agent's sandbox, returning the live handle."""
        from fleet_rlm.sandbox import manager as _mgr

        self.check_turn()
        with self._lock:
            self._refuse_unresolved_cleanup()
            if self._client is None:
                self._client = _mgr._shared_daytona_client(self.api_key, self.org_id)
            if self.parent is None and self._sandbox is None:
                self._snapshot_identity = _mgr.preflight(
                    self._client.snapshot,
                    name=self.snapshot,
                    api_key=self.api_key,
                    org_id=self.org_id,
                )
                if self.snapshot_resolved_callback is not None:
                    self.snapshot_resolved_callback(self._snapshot_identity)
            created = False
            if self._sandbox is None:
                self.check_turn()
                if self._creation_pending:
                    query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": self.agent_id})
                    matches = _mgr.with_retries(functools.partial(self._list_sandboxes, query))
                    if len(matches) != 1:
                        raise RuntimeError("Sandbox allocation outcome is uncertain; refusing a duplicate create")
                    self._sandbox = matches[0]
                    self._creation_pending = False
                    return self.ensure_sandbox()
                child = self.parent is not None
                root_sandbox = self.root.ensure_sandbox() if child else None
                params = CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    language="python",
                    volumes=self.volume_mounts(child=child),
                    auto_stop_interval=self.auto_stop_minutes,
                    linked_sandbox=root_sandbox.id if root_sandbox is not None and self.linked_children else None,
                    ephemeral=child or self.disposable,
                    auto_delete_interval=0 if child or self.disposable else -1,
                    ttl_minutes=_CHILD_TTL_MINUTES if child else _ROOT_TTL_MINUTES,
                    labels={
                        "fleet_session": self.session_id,
                        "fleet_agent": self.agent_id,
                        "fleet_parent": self.parent.agent_id if child else "root",
                    },
                )
                self.emit("sandbox_provisioning")
                self._persist_ownership_resource("create", None)
                self._creation_pending = True
                try:
                    self._sandbox = self._client.create(params)
                except Exception as exc:
                    if repl.is_definitive_client_error(exc):
                        self._persist_ownership_resource("confirmed", None)
                        self._creation_pending = False
                    raise
                self._creation_pending = False
                self._persist_ownership_resource("stop" if not child else "delete", self._sandbox.id)
                self.check_turn()
                self.emit("sandbox_ready", depth=self.depth)
                created = True
            if not created:
                self._resume_if_needed()
            return self._sandbox

    def _list_sandboxes(self: DaytonaSandboxManager, query: ListSandboxesQuery) -> list[Any]:
        assert self._client is not None
        return list(self._client.list(query))

    def _persist_ownership_resource(self: DaytonaSandboxManager, action: str, provider_id: str | None) -> None:
        """Persist this agent's cleanup intent before remote work, fenced by the Session epoch."""
        if self.ownership_resource_mutate is None and self.ownership_epoch is None:
            return
        if self.ownership_resource_mutate is None or self.ownership_epoch is None:
            raise RuntimeError("Durable sandbox ownership is unavailable")
        resource = (
            None
            if action == "confirmed"
            else {
                "allocation_nonce": self.agent_id,
                "provider_id": provider_id,
                "parent_agent_id": self.parent.agent_id if self.parent else None,
                "action": action,
                "intended_action": "delete" if self.parent is not None or self.disposable else "stop",
                "confirmed": False,
            }
        )
        if not self.ownership_resource_mutate(self.agent_id, resource):
            self._ownership_lost = True
            raise SandboxOwnershipLostError("Session ownership epoch changed; sandbox operation is fenced")

    def reconcile_sandbox_ownership(self: DaytonaSandboxManager, resources: list[dict[str, Any]]) -> None:
        """Confirm cleanup for bounded persisted Daytona resources before Session admission."""
        from fleet_rlm.sandbox import manager as _mgr

        if self.ownership_resource_mutate is None or self.ownership_epoch is None:
            raise RuntimeError("Durable sandbox ownership is unavailable")
        if not resources:
            return
        client = _mgr._shared_daytona_client(self.api_key, self.org_id)
        for entry in resources:
            agent_id = entry.get("agent_id")
            provider_id = entry.get("provider_id")
            action = entry.get("action")
            intended_action = entry.get("intended_action")
            nonce = entry.get("allocation_nonce")
            if (
                not isinstance(agent_id, str)
                or not isinstance(nonce, str)
                or action not in {"create", "stop", "delete"}
                or intended_action not in {"stop", "delete"}
            ):
                raise RuntimeError("Stored sandbox ownership record is invalid")
            try:
                if not isinstance(provider_id, str) or not provider_id:
                    query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": nonce})
                    matches = _mgr.with_retries(functools.partial(lambda q: list(client.list(q)), query))
                    if len(matches) != 1:
                        raise RuntimeError("Sandbox allocation outcome is uncertain; Session admission remains fenced")
                    sandbox = matches[0]
                    provider_id = getattr(sandbox, "id", None)
                    if not isinstance(provider_id, str) or not provider_id:
                        raise RuntimeError("Labeled sandbox has no provider identity")
                else:
                    sandbox = _mgr.with_retries(lambda provider_id=provider_id: client.get(provider_id))
                if intended_action == "delete":
                    _mgr.with_retries(lambda sandbox=sandbox: client.delete(sandbox, timeout=60, wait=True))
                else:
                    _mgr.with_retries(lambda sandbox=sandbox: client.stop(sandbox, timeout=60))
            except Exception as exc:
                if not _mgr.provider_not_found(exc):
                    raise
                _mgr._LOGGER.info(
                    "Recorded sandbox for Session %s agent %s is gone; treating absence as confirmed cleanup",
                    self.session_id,
                    agent_id,
                )
            if not self.ownership_resource_mutate(agent_id, None):
                raise RuntimeError("Session ownership epoch changed during sandbox reconciliation")

    def _resume_if_needed(self: DaytonaSandboxManager) -> None:
        """Start a sandbox the provider has stopped, archived, or paused since the handle was cached."""
        from fleet_rlm.sandbox import manager as _mgr

        sandbox = self._sandbox
        assert sandbox is not None and self._client is not None
        state = _sandbox_state(sandbox)
        deadline = _mgr.time.monotonic() + _mgr._STATE_SETTLE_SECONDS
        while state in _SETTLING_STATES:
            if _mgr.time.monotonic() >= deadline:
                raise RuntimeError(f"Sandbox {sandbox.id} did not settle from state {state!r}")
            _mgr.time.sleep(_mgr._STATE_POLL_SECONDS)
            self.check_turn()
            state = _sandbox_state(sandbox)
        if state in _FAILED_STATES:
            raise RuntimeError(f"Sandbox {sandbox.id} is in state {state!r} and cannot be resumed")
        if state in _RESUMABLE_STATES:
            span = mlflow.get_current_active_span()
            if span is not None:
                span.set_attribute("action", "resume")
            self.emit("sandbox_resuming", state=state)
            client = self._client
            _mgr.with_retries(lambda: client.start(sandbox), before_retry=self.check_turn)
            self._contexts.clear()

    def shutdown(self: DaytonaSandboxManager) -> bool:
        """Fence work and attempt every required cleanup, retaining unresolved identities."""
        if self.parent is None:
            self._cancelled.set()
            self._fence_executions()
            self.end_turn()
        with self._shutdown_lock:
            return self._shutdown_locked()

    def _shutdown_locked(self: DaytonaSandboxManager) -> bool:
        """Interrupt this resource first, then aggregate every required child cleanup."""
        from fleet_rlm.sandbox import manager as _mgr

        if self.parent is not None:
            with self.root._budget_lock:
                receipt = self.root._child_receipts.get(self.agent_id)
                if receipt is not None and receipt["status"] == "running":
                    receipt["status"] = "failed"
        with self._lock:
            self._cleanup_pending = True
            sandbox, client = self._sandbox, self._client
            interpreters = tuple(self._interpreters.values())
            self._contexts.clear()
            self._interpreters.clear()
            for interpreter in interpreters:
                interpreter._closed = True
        confirmed = False
        try:
            if self._ownership_lost or self.root._ownership_lost:
                raise SandboxOwnershipLostError("Sandbox cleanup belongs to the new Session owner")
            if self._creation_pending:
                self._persist_ownership_resource("create", None)
                if client is None:
                    raise RuntimeError("Pending allocation has no provider client")
                query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": self.agent_id})
                matches = _mgr.with_retries(functools.partial(self._list_sandboxes, query))
                if len(matches) != 1:
                    raise RuntimeError("Sandbox allocation outcome is uncertain")
                with self._lock:
                    self._sandbox = sandbox = matches[0]
                    self._creation_pending = False
            if sandbox is not None:
                if client is None:
                    raise RuntimeError("Sandbox cleanup has no provider client")
                self._persist_ownership_resource(
                    "delete" if self.parent is not None or self.disposable else "stop",
                    sandbox.id,
                )
                if self.parent is not None or self.disposable:
                    self._notify("sandbox_deleting", sandbox_id=sandbox.id)
                    try:
                        self._remote_exec(sandbox, CLEAR_WORKSPACE_CODE, timeout=60)
                    except Exception:
                        self._notify("workspace_cleanup_failed", sandbox_id=sandbox.id)
                    _mgr.with_retries(lambda: client.delete(sandbox, timeout=60, wait=True))
                    self._notify("sandbox_deleted", sandbox_id=sandbox.id)
                else:
                    _mgr.with_retries(lambda: client.stop(sandbox, timeout=60))
                with self._lock:
                    self._sandbox = None
                self._persist_ownership_resource("confirmed", None)
            confirmed = True
        except SandboxOwnershipLostError:
            with self._lock:
                self._ownership_lost = True
                self._sandbox = None
                self._creation_pending = False
            confirmed = True
        except Exception as exc:
            _mgr._LOGGER.warning(
                "Daytona sandbox cleanup unconfirmed for Session %s agent %s (%s)",
                self.session_id,
                self.agent_id,
                type(exc).__name__,
            )
            self._notify("sandbox_cleanup_failed", sandbox_id=getattr(sandbox, "id", None))
        if self.parent is None:
            with self._budget_lock:
                children = tuple(self._children.values())
            for child in children:
                try:
                    if child.shutdown() is not True:
                        confirmed = False
                except Exception as exc:
                    confirmed = False
                    _mgr._LOGGER.warning(
                        "Child sandbox shutdown failed for Session %s (%s)",
                        self.session_id,
                        type(exc).__name__,
                    )
        elif confirmed:
            with self.root._budget_lock:
                self.root._children.pop(self.agent_id, None)
        with self._lock:
            self._cleanup_pending = not confirmed
        return confirmed
