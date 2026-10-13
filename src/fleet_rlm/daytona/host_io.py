"""Concrete host-I/O sandbox lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fleet_rlm.daytona.leases import (
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    LeaseState,
    SandboxResource,
    _retain_provider_task,
)
from fleet_rlm.daytona.specs import (
    DaytonaSandboxSpec,
    SandboxPlatform,
    VolumeClient,
    VolumeConfig,
    _create_daytona_sandbox,
    _expected_workspace_mount,
    ensure_shared_volume_layout,
    get_or_create_volume_id,
    sandbox_state,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
)
from fleet_rlm.rlm.budget import current_host_action_deadline

if TYPE_CHECKING:
    from fleet_rlm.daytona.runtime import DaytonaRuntime

logger = logging.getLogger(__name__)
_WORKSPACE_IO_DELETE_GRACE_SECONDS = 15.0


class WorkspaceIO:
    """Own serialized host operations, warm reuse, and their outstanding work."""

    def __init__(
        self,
        *,
        lease_owner: DaytonaRuntime,
        platform: SandboxPlatform,
        volume_client: VolumeClient,
        volume_config: VolumeConfig,
        sandbox_spec: DaytonaSandboxSpec,
        admission: DaytonaAdmission,
        provider_tasks: set[asyncio.Future[Any]],
        leases: set[SandboxResource],
        idle_seconds: float,
        acquisition_timeout_seconds: float,
    ) -> None:
        self.accepting = True
        self._lease_owner = lease_owner
        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._sandbox_spec = sandbox_spec
        self._admission = admission
        self._provider_tasks = provider_tasks
        self._leases = leases
        self._workspace_io_idle_seconds = idle_seconds
        self._workspace_io_acquisition_timeout_seconds = acquisition_timeout_seconds
        self._workspace_io_contexts = 0
        self._workspace_io_pending: set[asyncio.Task[Any]] = set()
        self._workspace_io_idle_task: asyncio.Task[None] | None = None
        self._warm_workspace_io_sandboxes: dict[UUID, tuple[Any, SandboxResource, float]] = {}
        self._warm_workspace_io_lock = asyncio.Lock()
        self._prewarming_workspaces: set[UUID] = set()

    def _workspace_io_resources(self) -> tuple[SandboxResource, ...]:
        return tuple(lease for lease in self._leases if lease.role == "host_io")

    def _workspace_io_lease(self, sandbox: Any, permit: DaytonaAdmissionPermit) -> SandboxResource:
        lease = SandboxResource(
            owner=self._lease_owner,
            sandbox=sandbox,
            sandbox_id=getattr(sandbox, "id", None),
            platform=self._platform,
            permit=permit,
            role="host_io",
            timeout_s=_WORKSPACE_IO_DELETE_GRACE_SECONDS,
        )
        return lease

    async def _close_workspace_io_lease(self, lease: SandboxResource, *, deadline: float | None = None) -> bool:
        receipt = await lease.aclose(deadline=deadline)
        if not receipt:
            logger.warning(
                "Workspace I/O Sandbox deletion not confirmed absent within grace period",
                extra={"sandbox_id": lease._sandbox_id, "provider_error": str(lease.failure)},
            )
        return receipt and not lease.has_pending_ownership

    def _retain_workspace_io_task(self, task: asyncio.Task[Any]) -> None:
        self._workspace_io_pending.add(task)
        task.add_done_callback(self._workspace_io_pending.discard)
        _retain_provider_task(task, self._provider_tasks)

    async def _cancel_workspace_io_idle(self) -> None:
        task = self._workspace_io_idle_task
        self._workspace_io_idle_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _schedule_workspace_io_idle(self, workspace_id: UUID, lease: SandboxResource) -> None:
        async def expire() -> None:
            try:
                await asyncio.sleep(self._workspace_io_idle_seconds)
                async with self._warm_workspace_io_lock:
                    entry = self._warm_workspace_io_sandboxes.get(workspace_id)
                    if self.accepting and entry is not None and entry[1] is lease:
                        self._warm_workspace_io_sandboxes.pop(workspace_id)
                        await self._close_workspace_io_lease(lease)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Workspace idle cleanup remains runtime-owned", extra={"error_type": type(exc).__name__})

        self._workspace_io_idle_task = asyncio.create_task(expire(), name="fleet-daytona-workspace-io-idle")

    def schedule_workspace_prewarm(self, workspace_id: UUID) -> asyncio.Task[None] | None:
        """Create the Workspace I/O Sandbox in the background so the next Turn reuses it.

        Returns None when a warm Sandbox or an in-flight pre-warm already exists. The task is
        retained as a provider task, not in the pending set: a Turn holds the warm lock while it
        awaits that set, and the pre-warm needs the same lock, so sharing the set would deadlock.
        """
        if not self.accepting or workspace_id in self._warm_workspace_io_sandboxes:
            return None
        if workspace_id in self._prewarming_workspaces:
            return None
        self._prewarming_workspaces.add(workspace_id)

        async def prewarm() -> None:
            try:
                async with self.open_workspace_sandbox(workspace_id, purpose="workspace-prewarm", reuse_warm=True):
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Workspace I/O pre-warm failed; the next Turn acquires normally",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                self._prewarming_workspaces.discard(workspace_id)

        task = asyncio.create_task(prewarm(), name="fleet-daytona-workspace-io-prewarm")
        _retain_provider_task(task, self._provider_tasks)
        return task

    @contextlib.asynccontextmanager
    async def open_workspace_sandbox(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
        reuse_warm: bool = False,
    ) -> AsyncIterator[Any]:
        """Serialize local Workspace I/O through one bounded, runtime-owned slot."""
        if not self.accepting:
            raise RuntimeError("Daytona runtime is not accepting Workspace I/O")
        deadline = asyncio.get_running_loop().time() + self._workspace_io_acquisition_timeout_seconds
        action_deadline = current_host_action_deadline()
        if action_deadline is not None:
            deadline = min(deadline, action_deadline)
        if deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("Workspace I/O acquisition timed out")
        retain_warm = reuse_warm and self._admission.max_active_leases > 1
        locked = False
        ready = False
        lease: SandboxResource | None = None
        self._workspace_io_contexts += 1
        try:
            # End this timeout before yielding: file operations keep their own
            # bounds. The gate prevents overlapping use and replacement races.
            async with asyncio.timeout_at(deadline):
                await self._warm_workspace_io_lock.acquire()
                locked = True
                if not self.accepting:
                    raise RuntimeError("Daytona runtime closed during Workspace I/O acquisition")
                await self._cancel_workspace_io_idle()
                for task in tuple(self._workspace_io_pending):
                    await asyncio.shield(task)
                entry = self._warm_workspace_io_sandboxes.get(workspace_id) if retain_warm else None
                sandbox: Any = None
                if entry is not None and entry[1].state is LeaseState.OPEN:
                    candidate, candidate_lease, _ = entry
                    lease = candidate_lease
                    try:
                        await candidate.refresh_data()
                        if sandbox_state(candidate) == "stopped":
                            await self._platform.start(candidate.id)
                            await candidate.refresh_data()
                        if sandbox_state(candidate) == "running":
                            sandbox, lease = candidate, candidate_lease
                    except Exception:
                        # Failed probes do not release permits or ownership.
                        pass
                if sandbox is None:
                    if lease is not None:
                        lease = None
                    self._warm_workspace_io_sandboxes.clear()
                    while resources := self._workspace_io_resources():
                        for obsolete in resources:
                            await self._close_workspace_io_lease(obsolete, deadline=deadline)
                        if self._workspace_io_resources():
                            await asyncio.sleep(0.05)
                    volume_config = self._volume_config
                    volume_id = await get_or_create_volume_id(self._volume_client, volume_config)
                    expected = _expected_workspace_mount(volume_config, volume_id, workspace_id)
                    permit = await self._admission.acquire(deadline=deadline, host_io=True)
                    if not self.accepting:
                        permit.release()
                        raise RuntimeError("Daytona runtime closed during Workspace I/O admission")
                    create_task = asyncio.create_task(
                        _create_daytona_sandbox(
                            self._platform,
                            expected,
                            labels={
                                "fleet-package": "fleet_rlm",
                                "purpose": purpose,
                                "workspace_id": str(workspace_id),
                            },
                            ephemeral=not retain_warm,
                        ),
                        name="fleet-daytona-workspace-io-create",
                    )
                    _retain_provider_task(create_task, self._provider_tasks)
                    try:
                        sandbox = await asyncio.shield(create_task)
                    except BaseException:

                        async def settle_late_create() -> None:
                            try:
                                late_sandbox = await create_task
                            except BaseException:
                                permit.release()
                                return
                            await self._close_workspace_io_lease(self._workspace_io_lease(late_sandbox, permit))

                        self._retain_workspace_io_task(
                            asyncio.create_task(settle_late_create(), name="fleet-daytona-workspace-io-late-create")
                        )
                        raise
                    lease = self._workspace_io_lease(sandbox, permit)
                    await sandbox.refresh_data()
                    if sandbox_state(sandbox) != "running":
                        raise RuntimeError("Workspace I/O Sandbox did not reach running state")
                    verify_sandbox_workspace_mount(sandbox, expected)
                    verify_sandbox_spec(sandbox, self._sandbox_spec)
                    await ensure_shared_volume_layout(sandbox, volume_config.paths())
                if not self.accepting:
                    raise RuntimeError("Daytona runtime closed during Workspace I/O preparation")
                assert lease is not None
                if retain_warm:
                    self._warm_workspace_io_sandboxes[workspace_id] = (sandbox, lease, time.monotonic())
                ready = True
            yield sandbox
        finally:
            try:
                if lease is not None:
                    if ready and retain_warm and self.accepting:
                        self._schedule_workspace_io_idle(workspace_id, lease)
                    else:
                        self._warm_workspace_io_sandboxes.pop(workspace_id, None)
                        if not ready:
                            # Failed preparation returns within its bound;
                            # the runtime keeps ownership of asynchronous cleanup.
                            self._retain_workspace_io_task(
                                asyncio.create_task(
                                    self._close_workspace_io_lease(lease),
                                    name="fleet-daytona-workspace-io-failed-prepare",
                                )
                            )
                        else:
                            try:
                                await self._close_workspace_io_lease(lease)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.warning(
                                    "Workspace I/O cleanup remains runtime-owned",
                                    extra={"sandbox_id": lease._sandbox_id, "error_type": type(exc).__name__},
                                )
            finally:
                self._workspace_io_contexts -= 1
                if locked:
                    self._warm_workspace_io_lock.release()
