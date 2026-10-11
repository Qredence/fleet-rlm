"""Disposable child sandboxes, file bounds, and late cleanup ownership."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Condition, Lock, get_ident
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.errors import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeNotStartedError,
)
from fleet_rlm.daytona.interpreter import (
    SyncBridgeDispatcher,
    sandbox_interpreter_factory,
)
from fleet_rlm.daytona.leases import (
    _CLEANUP_EXCEPTIONS,
    CHILD_CLEANUP_RESULT_TIMEOUT_S,
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    DaytonaAdmissionTimeoutError,
    LeaseState,
    _retain_provider_task,
    cleanup_after_failed_acquire,
    cleanup_child_runtime_async,
    close_child_runtime_sync,
)
from fleet_rlm.daytona.provider import list_files, read_file, write_file
from fleet_rlm.daytona.specs import (
    DaytonaEnvironmentProfile,
    SandboxPlatform,
)
from fleet_rlm.daytona.specs import ensure_directories as _ensure_directories
from fleet_rlm.daytona.specs import is_not_found as _is_not_found
from fleet_rlm.daytona.specs import sandbox_filesystem as _sandbox_filesystem
from fleet_rlm.paths import SESSION_WORKSPACE_MOUNT_PATH
from fleet_rlm.rlm.ownership import OwnedEffect
from fleet_rlm.sessions.bindings import (
    session_workspace_volume_subpath,
)

_CHILD_ADMISSION_WAIT_SECONDS = 5.0


_CHILD_STAGE_MAX_FILES = 256


_CHILD_RESULT_MAX_BYTES = 16 * 1024 * 1024


_CHILD_RESULT_MAX_ENTRIES = 1024


_PROVIDER_CHILD_STAGE_MAX_BYTES = 64 * 1024 * 1024


ChildRuntimeLeaseState = LeaseState


@dataclass(slots=True, eq=False)
class ChildRuntimeLease:
    """One synchronously usable child interpreter and its owned cleanup action."""

    interpreter: Any
    sandbox_id: str
    volume_id: str
    volume_subpath: str
    _close: Callable[[], None] = field(repr=False)
    _data_path: str = field(default="", repr=False, kw_only=True)
    _stage_files: Callable[[Mapping[str, bytes]], None] | None = field(default=None, repr=False, kw_only=True)
    _read_result_files: Callable[[Sequence[str]], Mapping[str, bytes]] | None = field(
        default=None, repr=False, kw_only=True
    )
    _state: ChildRuntimeLeaseState = field(default=ChildRuntimeLeaseState.OPEN, init=False, repr=False)
    _close_error: BaseException | None = field(default=None, init=False, repr=False)
    _condition: Condition = field(default_factory=Condition, init=False, repr=False)
    _closing_thread_id: int | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> ChildRuntimeLeaseState:
        with self._condition:
            return self._state

    @property
    def data_path(self) -> str:
        """Absolute child-local directory used by staging and result harvesting."""
        return self._data_path

    def stage_files(self, files: Mapping[str, bytes]) -> None:
        """Copy bounded relative inputs into this child's private data directory."""
        self._require_open()
        if self._stage_files is None:
            raise RuntimeError("child lease does not support private file staging")
        self._stage_files(files)

    def read_result_files(self, paths: Sequence[str]) -> Mapping[str, bytes]:
        """Read bounded relative result files before the child lease is closed."""
        self._require_open()
        if self._read_result_files is None:
            raise RuntimeError("child lease does not support result file harvesting")
        return self._read_result_files(paths)

    def _require_open(self) -> None:
        if self.state is not ChildRuntimeLeaseState.OPEN:
            raise RuntimeError("child lease file operations require an open lease")

    @property
    def close_error(self) -> BaseException | None:
        with self._condition:
            return self._close_error

    def close(self) -> None:
        with self._condition:
            if self._state is ChildRuntimeLeaseState.CLOSED:
                return
            if self._state is ChildRuntimeLeaseState.CLOSING:
                if self._closing_thread_id == get_ident():
                    raise RuntimeError("recursive child lease close is not reentrant")
                while self._state is ChildRuntimeLeaseState.CLOSING:
                    self._condition.wait()
                if self._state is ChildRuntimeLeaseState.CLOSED:
                    return
                if self._state is ChildRuntimeLeaseState.FAILED:
                    error = self._close_error
                    if error is None:
                        raise RuntimeError("recursive child lease close failed")
                    raise error
            if self._state is ChildRuntimeLeaseState.FAILED:
                error = self._close_error
                if error is None:
                    raise RuntimeError("recursive child lease close failed")
                raise error
            self._state = ChildRuntimeLeaseState.CLOSING
            self._closing_thread_id = get_ident()

        try:
            self._close()
        except BaseException as exc:
            with self._condition:
                self._close_error = exc
                self._state = ChildRuntimeLeaseState.FAILED
                self._closing_thread_id = None
                self._condition.notify_all()
            raise
        else:
            with self._condition:
                self._state = ChildRuntimeLeaseState.CLOSED
                self._closing_thread_id = None
                self._condition.notify_all()


class LateCleanupOwner:
    """Keep late provider work owned until its cleanup future settles."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop, wait_timeout_s: float) -> None:
        self._loop = loop
        self._unresolved_leases: dict[int, Any] = {}
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
        with self._lock:
            for future in tuple(self._pending):
                if not future.done():
                    continue
                try:
                    error = future.exception()
                except _CLEANUP_EXCEPTIONS as exc:
                    error = exc
                if error is not None and self._error is None:
                    self._error = error
                self._pending.discard(future)
            return self._error, any(not future.done() for future in self._pending)

    @staticmethod
    def _complete(marker: Future[None], error: BaseException | None = None) -> None:
        if marker.done():
            return
        if error is None:
            marker.set_result(None)
        else:
            marker.set_exception(error)

    def retain(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.add(future)

        def settled(done: Future[Any]) -> None:
            try:
                error = done.exception()
            except _CLEANUP_EXCEPTIONS as exc:
                self._record_error(exc)
            else:
                if error is not None:
                    self._record_error(error)
            with self._lock:
                self._pending.discard(done)

        future.add_done_callback(settled)

    def adopt_late_acquisition(
        self,
        acquisition: Future[Any],
        close_lease: Callable[[Any], None],
    ) -> None:
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
            try:
                lease = done.result()
            except ChildRuntimeCleanupError as exc:
                self._record_error(exc)
                self._complete(marker)
                return
            except _CLEANUP_EXCEPTIONS:
                self._complete(marker)
                return

            self._unresolved_leases[id(lease)] = lease

            async def close() -> None:
                try:
                    await asyncio.to_thread(close_lease, lease)
                except _CLEANUP_EXCEPTIONS as exc:
                    self._record_error(exc)
                else:
                    self._unresolved_leases.pop(id(lease), None)
                finally:
                    self._complete(marker)

            coroutine = close()
            try:
                if self._loop.is_closed() or not self._loop.is_running():
                    raise RuntimeError("child cleanup application loop is unavailable")
                cleanup = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
                self.retain(cleanup)
            except _CLEANUP_EXCEPTIONS as exc:
                coroutine.close()
                self._record_error(exc)
                self._complete(marker, exc)

        acquisition.add_done_callback(close_late)

    def raise_if_failed(self) -> None:
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def has_unresolved(self) -> bool:
        with self._lock:
            return bool(self._unresolved_leases or any(not future.done() for future in self._pending))

    def wait_owned(self) -> None:
        wait_deadline = time.monotonic() + max(self._wait_timeout_s, 1.0)
        while True:
            with self._lock:
                pending = tuple(future for future in self._pending if not future.done())
            if not pending:
                break
            remaining = max(0.0, wait_deadline - time.monotonic())
            _, still_pending = wait(pending, timeout=remaining)
            if still_pending:
                self._record_error(TimeoutError("recursive child cleanup quarantine timed out"))
                break
        self.raise_if_failed()


def sandbox_id_for(sandbox: Any) -> str:
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def _validate_child_relative_paths(paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes)):
        raise TypeError("child file paths must be a sequence of relative paths")
    normalized: list[str] = []
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
            raise ValueError("child file paths must be non-empty relative POSIX paths")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.split("/")):
            raise ValueError(f"child file path is not safely relative: {raw_path}")
        normalized_path = str(path)
        if normalized_path in normalized:
            raise ValueError("child file paths must be unique")
        normalized.append(normalized_path)
    if len(normalized) > _CHILD_STAGE_MAX_FILES:
        raise ValueError("too many child files")
    return tuple(normalized)


def _validate_child_file_mapping(files: Mapping[str, bytes], *, max_bytes: int) -> dict[str, bytes]:
    if not isinstance(files, Mapping):
        raise TypeError("child files must be a mapping of relative paths to bytes")
    paths = _validate_child_relative_paths(tuple(files))
    validated: dict[str, bytes] = {}
    total = 0
    for path in paths:
        content = files[path]
        if not isinstance(content, bytes):
            raise TypeError("child file contents must be bytes")
        total += len(content)
        if total > max_bytes:
            raise ValueError("child files exceed the configured size limit")
        validated[path] = content
    return validated


def _file_info_value(info: Any, name: str) -> Any:
    value = getattr(info, name, None)
    if value is not None:
        return value
    if isinstance(info, Mapping):
        value = info.get(name)
        if value is not None:
            return value
    additional = getattr(info, "additional_properties", None)
    if isinstance(additional, Mapping):
        return additional.get(name)
    return None


def _file_info_is_symlink(info: Any) -> bool:
    if info is None:
        return False
    if bool(_file_info_value(info, "is_symlink")) or bool(_file_info_value(info, "symlink")):
        return True
    kind = _file_info_value(info, "type")
    if isinstance(kind, str) and kind.lower() in {"symlink", "symbolic_link", "symbolic link"}:
        return True
    mode = _file_info_value(info, "mode")
    if isinstance(mode, str):
        try:
            normalized_mode = int(mode.strip(), 0 if mode.strip().startswith("0o") else 8)
        except ValueError:
            return False
    else:
        normalized_mode = mode
    return isinstance(normalized_mode, int) and normalized_mode & 0o170000 == 0o120000


async def _assert_no_child_symlink(
    fs: Any,
    root: str,
    relative: str = "",
    *,
    allow_missing: bool = False,
) -> None:
    """Check every existing absolute and relative component before child-file I/O."""
    get_info = getattr(fs, "get_file_info", None)
    if not callable(get_info):
        raise ValueError("child filesystem cannot verify symlink safety")
    current = PurePosixPath("/")
    paths: list[str] = []
    for part in PurePosixPath(root).parts[1:]:
        current /= part
        paths.append(str(current))
    if relative:
        for part in PurePosixPath(relative).parts:
            current /= part
            paths.append(str(current))
    for path in paths:
        try:
            info = await get_info(path)
        except Exception as exc:
            if allow_missing and _is_not_found(exc):
                continue
            raise
        if info is None:
            if allow_missing:
                continue
            raise ValueError("child filesystem could not verify path metadata")
        if _file_info_is_symlink(info):
            raise ValueError("child path contains an unsafe symlink")


def require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


async def _close_child_lease(lease: Any) -> Any:
    """Close a sync or async child lease."""
    close = getattr(lease, "close", None)
    if not callable(close):
        raise TypeError("child lease does not expose close()")
    if inspect.iscoroutinefunction(close):
        return await close()
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass(slots=True)
class _ChildCleanupRecord:
    platform: SandboxPlatform
    sandbox: Any
    sandbox_id: str
    mount_path: str | None
    permit: DaytonaAdmissionPermit
    lease: ChildRuntimeLease | None = None
    close_task: asyncio.Task[Any] | None = None
    session_id: UUID | None = None


logger = logging.getLogger(__name__)


class ChildSandboxes:
    """Own child acquisition records and factories until cleanup is confirmed."""

    def __init__(
        self,
        *,
        platform: SandboxPlatform,
        admission: DaytonaAdmission,
        dispatcher: SyncBridgeDispatcher | None,
        provider_tasks: set[asyncio.Future[Any]],
    ) -> None:
        self.accepting = True
        self._platform = platform
        self._admission = admission
        self._dispatcher = dispatcher
        self._provider_tasks = provider_tasks
        self._lock = asyncio.Lock()
        self._child_cleanup_records: dict[str, _ChildCleanupRecord] = {}
        self._unidentified_child_sandboxes: list[tuple[SandboxPlatform, Any, DaytonaAdmissionPermit]] = []
        self._child_factories: set[Any] = set()

    def _forget_child_cleanup_if_settled(self, sandbox_id: str) -> None:
        record = self._child_cleanup_records.get(sandbox_id)
        if record is not None and record.permit._released:
            self._child_cleanup_records.pop(sandbox_id, None)

    def _adopt_late_child_create(
        self,
        creation: asyncio.Task[Any],
        *,
        platform: SandboxPlatform,
        permit: DaytonaAdmissionPermit,
        mount_path: str | None,
        retain_pending_cleanup: Callable[[Future[Any]], None] | None,
    ) -> None:
        marker: Future[None] = Future()
        if retain_pending_cleanup is not None:
            retain_pending_cleanup(marker)

        async def settle() -> None:
            try:
                sandbox = await asyncio.shield(creation)
            except BaseException:
                permit.release()
                marker.set_result(None)
                return
            sandbox_id: str | None = None
            try:
                sandbox_id = sandbox_id_for(sandbox)
            except BaseException:
                self._unidentified_child_sandboxes.append((platform, sandbox, permit))
            else:
                self._child_cleanup_records[sandbox_id] = _ChildCleanupRecord(
                    platform, sandbox, sandbox_id, mount_path, permit
                )
            try:
                await cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit)
            except BaseException as exc:
                logger.warning(
                    "Late child creation cleanup remains runtime-owned",
                    extra={"sandbox_id": sandbox_id},
                )
                marker.set_exception(exc)
            else:
                if sandbox_id is None:
                    self._forget_unidentified_child(platform, sandbox, permit)
                marker.set_result(None)
            finally:
                if sandbox_id is not None:
                    self._forget_child_cleanup_if_settled(sandbox_id)

        task = asyncio.create_task(settle(), name="fleet-daytona-late-child-create")
        _retain_provider_task(task, self._provider_tasks)

    def _forget_unidentified_child(
        self,
        platform: SandboxPlatform,
        sandbox: Any,
        permit: DaytonaAdmissionPermit,
    ) -> None:
        try:
            self._unidentified_child_sandboxes.remove((platform, sandbox, permit))
        except ValueError:
            return

    async def _retry_unidentified_child_sandboxes(self, *, deadline: float) -> None:
        pending = tuple(self._unidentified_child_sandboxes)
        for platform, sandbox, permit in pending:
            if permit._released:
                self._forget_unidentified_child(platform, sandbox, permit)
                continue
            if asyncio.get_running_loop().time() >= deadline:
                return
            sandbox_id: str | None = None
            with contextlib.suppress(BaseException):
                sandbox_id = sandbox_id_for(sandbox)
            try:
                await cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit)
            except BaseException:
                logger.warning(
                    "Unidentified child cleanup remains runtime-owned",
                    extra={"sandbox_id": sandbox_id},
                )
                continue
            self._forget_unidentified_child(platform, sandbox, permit)

    async def _retry_child_cleanup_records(self, *, deadline: float) -> None:
        await self._retry_unidentified_child_sandboxes(deadline=deadline)
        for sandbox_id, record in tuple(self._child_cleanup_records.items()):
            pending_close = record.close_task
            if pending_close is not None and not pending_close.done():
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                if remaining:
                    await asyncio.wait({pending_close}, timeout=remaining)
                continue
            if record.permit._released:
                self._forget_child_cleanup_if_settled(sandbox_id)
                continue
            task = asyncio.create_task(
                cleanup_child_runtime_async(
                    platform=record.platform,
                    sandbox=record.sandbox,
                    sandbox_id=sandbox_id,
                    mount_path=record.mount_path,
                    permit=record.permit,
                ),
                name="fleet-daytona-child-cleanup-retry",
            )
            record.close_task = task
            _retain_provider_task(task, self._provider_tasks)

            def settled(completed: asyncio.Task[Any], sid: str = sandbox_id) -> None:
                if not completed.cancelled():
                    with contextlib.suppress(BaseException):
                        completed.exception()
                self._forget_child_cleanup_if_settled(sid)

            task.add_done_callback(settled)
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            if remaining:
                await asyncio.wait({task}, timeout=remaining)

    async def _acquire_child_runtime(
        self,
        *,
        volume_id: str | None,
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        workspace_id: UUID,
        run_id: UUID,
        session_id: UUID | None = None,
        call_index: int,
        deadline: float,
        execution_output_cap: int,
        retain_pending_cleanup: Callable[[Future[Any]], None],
        is_authorized: Callable[[], bool] | None = None,
    ) -> ChildRuntimeLease:
        loop = asyncio.get_running_loop()
        platform = self._platform
        admission = self._admission
        dispatcher = self._dispatcher
        require_authorized(is_authorized)
        if not isinstance(profile, DaytonaEnvironmentProfile):
            profile = DaytonaEnvironmentProfile(str(profile))
        semantic = profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
        if not semantic and (not volume_id or session_id is None):
            raise ValueError("WorkspaceChild requires a Volume binding")
        if not semantic:
            assert session_id is not None
        admission_deadline = min(deadline, time.monotonic() + _CHILD_ADMISSION_WAIT_SECONDS)
        permit = await admission.acquire(deadline=admission_deadline)
        sandbox: Any | None = None
        sandbox_id: str | None = None
        if semantic:
            subpath = ""
        else:
            assert session_id is not None
            subpath = session_workspace_volume_subpath(workspace_id, session_id)
        scratch_path = f"/tmp/fleet/child-data/{run_id}/{call_index}"
        # Keep staged inputs and child outputs in the interpreter's
        # invocation-scoped scratch so model code can resolve the relative
        # paths from FLEET_RUN_SCRATCH.
        child_files_path = scratch_path
        try:
            require_authorized(is_authorized)
            labels = {"fleet.runtime": "recursive-child"}
            if semantic:
                labels["fleet.profile"] = profile.value
            create_kwargs: dict[str, Any] = {
                "profile": profile,
                "volume_id": None if semantic else volume_id,
                "mount_path": None if semantic else SESSION_WORKSPACE_MOUNT_PATH,
                "volume_subpath": None if semantic else subpath,
                "labels": labels,
                "with_volume": not semantic,
                "ephemeral": True,
                "auto_stop_interval": 0,
            }
            if semantic:
                create_kwargs["network_block_all"] = True
            creation = asyncio.create_task(platform.create(**create_kwargs), name="fleet-daytona-child-create")
            _retain_provider_task(creation, self._provider_tasks)
            try:
                async with asyncio.timeout_at(deadline):
                    sandbox = await asyncio.shield(creation)
            except (TimeoutError, asyncio.CancelledError):
                self._adopt_late_child_create(
                    creation,
                    platform=platform,
                    permit=permit,
                    mount_path=None if semantic else scratch_path,
                    retain_pending_cleanup=retain_pending_cleanup,
                )
                permit = None
                raise
            sandbox_id = sandbox_id_for(sandbox)
            child_sandbox_id = sandbox_id
            self._child_cleanup_records[child_sandbox_id] = _ChildCleanupRecord(
                platform, sandbox, child_sandbox_id, None if semantic else scratch_path, permit, session_id=session_id
            )
            require_authorized(is_authorized)
            if not semantic:
                fs = _sandbox_filesystem(sandbox)
                await _ensure_directories(
                    fs,
                    (
                        "/tmp/fleet",
                        "/tmp/fleet/child-data",
                        str(PurePosixPath(scratch_path).parent),
                        scratch_path,
                    ),
                )
            interpreter = sandbox_interpreter_factory(
                sandbox,
                loop=loop,
                dispatcher=dispatcher,
                execution_output_cap=execution_output_cap,
                run_id=run_id,
                call_index=call_index,
            )

            def run_fs(operation: Coroutine[Any, Any, Any]) -> Any:
                try:
                    future = asyncio.run_coroutine_threadsafe(operation, loop)
                except BaseException:
                    operation.close()
                    raise
                return future.result(timeout=max(0.0, deadline - time.monotonic()))

            def stage_child_files(files: Mapping[str, bytes]) -> None:
                validated = _validate_child_file_mapping(files, max_bytes=_PROVIDER_CHILD_STAGE_MAX_BYTES)

                async def stage() -> None:
                    fs = _sandbox_filesystem(sandbox)
                    await _assert_no_child_symlink(fs, child_files_path, allow_missing=True)
                    await _ensure_directories(
                        fs,
                        (
                            "/tmp/fleet",
                            "/tmp/fleet/child-data",
                            str(PurePosixPath(child_files_path).parent),
                            child_files_path,
                        ),
                    )
                    for relative, content in validated.items():
                        await _assert_no_child_symlink(
                            fs,
                            child_files_path,
                            relative,
                            allow_missing=True,
                        )
                        destination = f"{child_files_path}/{relative}"
                        parent = str(PurePosixPath(destination).parent)
                        await _ensure_directories(fs, (parent,))
                        await write_file(sandbox, destination, content)

                run_fs(stage())

            def read_child_results(paths: Sequence[str]) -> Mapping[str, bytes]:
                validated_paths = _validate_child_relative_paths(paths)

                async def read() -> Mapping[str, bytes]:
                    fs = _sandbox_filesystem(sandbox)
                    await _assert_no_child_symlink(fs, child_files_path)
                    # Daytona lists one level when no depth is sent, so a
                    # root-only listing never sees nested declared files.
                    # Look up each declared file in its own parent instead.
                    listings: dict[str, dict[str, Any]] = {}

                    async def parent_entries(parent: str) -> dict[str, Any]:
                        cached = listings.get(parent)
                        if cached is not None:
                            return cached
                        try:
                            entries = await list_files(sandbox, parent, depth=1)
                        except Exception as exc:
                            if not _is_not_found(exc):
                                raise
                            entries = []
                        if len(entries) > _CHILD_RESULT_MAX_ENTRIES:
                            raise ValueError("child result directory contains too many entries")
                        by_path: dict[str, Any] = {}
                        for entry in entries:
                            path = getattr(entry, "path", None)
                            if not isinstance(path, str) or not path:
                                name = getattr(entry, "name", None)
                                if not isinstance(name, str) or not name:
                                    continue
                                path = f"{parent}/{name}"
                            by_path[path.rstrip("/")] = entry
                        listings[parent] = by_path
                        return by_path

                    output: dict[str, bytes] = {}
                    total = 0
                    for relative in validated_paths:
                        target = f"{child_files_path}/{relative}"
                        await _assert_no_child_symlink(fs, child_files_path, relative, allow_missing=True)
                        entry = (await parent_entries(str(PurePosixPath(target).parent))).get(target)
                        if entry is None or bool(getattr(entry, "is_dir", False)):
                            raise ValueError(f"child result file is missing or not a file: {relative}")
                        if _file_info_is_symlink(entry):
                            raise ValueError("child result path contains an unsafe symlink")
                        declared_size = getattr(entry, "size", None)
                        if isinstance(declared_size, int) and not isinstance(declared_size, bool):
                            if declared_size < 0 or declared_size > _CHILD_RESULT_MAX_BYTES:
                                raise ValueError("child result files exceed the configured size limit")
                            if total + declared_size > _CHILD_RESULT_MAX_BYTES:
                                raise ValueError("child result files exceed the configured size limit")
                        content = await read_file(sandbox, target)
                        total += len(content)
                        if len(content) > _PROVIDER_CHILD_STAGE_MAX_BYTES or total > _CHILD_RESULT_MAX_BYTES:
                            raise ValueError("child result files exceed the configured size limit")
                        output[relative] = content
                    return output

                return run_fs(read())

            def close() -> None:
                try:
                    close_child_runtime_sync(
                        loop=loop,
                        platform=platform,
                        sandbox=sandbox,
                        sandbox_id=child_sandbox_id,
                        mount_path=None if semantic else scratch_path,
                        interpreter=interpreter,
                        permit=permit,
                        retain_pending_cleanup=retain_pending_cleanup,
                    )
                finally:
                    loop.call_soon_threadsafe(self._forget_child_cleanup_if_settled, child_sandbox_id)

            return ChildRuntimeLease(
                interpreter,
                child_sandbox_id,
                "" if semantic else (volume_id or ""),
                subpath,
                close,
                _data_path=child_files_path,
                _stage_files=stage_child_files,
                _read_result_files=read_child_results,
            )
        except BaseException:
            if permit is None:
                raise
            if sandbox is not None and sandbox_id is None:
                with contextlib.suppress(BaseException):
                    sandbox_id = sandbox_id_for(sandbox)
                if sandbox_id is None:
                    self._unidentified_child_sandboxes.append((platform, sandbox, permit))
            if sandbox is not None and sandbox_id is not None and sandbox_id not in self._child_cleanup_records:
                self._child_cleanup_records[sandbox_id] = _ChildCleanupRecord(
                    platform, sandbox, sandbox_id, None if semantic else scratch_path, permit, session_id=session_id
                )
            try:
                cleanup = OwnedEffect.start(cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit))
                await cleanup.settle()
            except BaseException as cleanup_error:
                raise ChildRuntimeCleanupError("recursive child cleanup failed") from cleanup_error
            finally:
                if sandbox_id is not None:
                    self._forget_child_cleanup_if_settled(sandbox_id)
            raise

    def build_child_factory(
        self,
        *,
        volume_id: str | None,
        workspace_id: UUID,
        run_id: UUID,
        deadline: float,
        execution_output_cap: int,
        session_id: UUID | None = None,
        is_authorized: Callable[[], bool] | None = None,
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        semantic_child_available: bool = True,
    ) -> Callable[..., Any]:
        """Build one synchronous recursion factory owned by this runtime."""
        loop = asyncio.get_running_loop()
        late_owner = LateCleanupOwner(loop=loop, wait_timeout_s=CHILD_CLEANUP_RESULT_TIMEOUT_S)
        runtime = self

        class RuntimeChildFactory:
            def __call__(
                self,
                call_index: int,
                *,
                profile: DaytonaEnvironmentProfile | str | None = None,
            ) -> ChildRuntimeLease:
                if not runtime.accepting:
                    raise RuntimeError("Daytona runtime is not accepting child leases")
                selected_profile = profile if profile is not None else profile_default
                if not isinstance(selected_profile, DaytonaEnvironmentProfile):
                    selected_profile = DaytonaEnvironmentProfile(str(selected_profile))
                if selected_profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD and not semantic_child_available:
                    raise ValueError("SemanticChild requires FLEET_DAYTONA_CHILD_SNAPSHOT")
                acquisition_coroutine = runtime._acquire_child_runtime(
                    volume_id=volume_id,
                    profile=selected_profile,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=run_id,
                    call_index=call_index,
                    deadline=deadline,
                    execution_output_cap=execution_output_cap,
                    is_authorized=is_authorized,
                    retain_pending_cleanup=late_owner.retain,
                )
                try:
                    acquisition = asyncio.run_coroutine_threadsafe(acquisition_coroutine, loop)
                except BaseException as exc:
                    acquisition_coroutine.close()
                    raise ChildRuntimeCleanupError("recursive child runtime acquisition failed") from exc
                try:
                    lease = acquisition.result(timeout=max(0.0, deadline - time.monotonic()))
                except DaytonaAdmissionTimeoutError:
                    if time.monotonic() < deadline:
                        raise ChildRuntimeNotStartedError("child capacity is unavailable") from None
                    raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None
                except TimeoutError:
                    late_owner.adopt_late_acquisition(acquisition, lambda pending: pending.close())
                    raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

                registration = asyncio.run_coroutine_threadsafe(runtime._register_child_lease(lease), loop)
                try:
                    registration.result(timeout=max(0.0, deadline - loop.time()))
                except BaseException:
                    lease.close()
                    raise
                return lease

            def wait_owned(self) -> None:
                try:
                    late_owner.wait_owned()
                except ChildRuntimeCleanupError:
                    if (
                        late_owner.has_unresolved()
                        or runtime._child_cleanup_records
                        or runtime._unidentified_child_sandboxes
                    ):
                        raise
                loop.call_soon_threadsafe(runtime._child_factories.discard, self)

            def raise_if_cleanup_failed(self) -> None:
                late_owner.raise_if_failed()

        profile_default = profile
        factory = RuntimeChildFactory()
        self._child_factories.add(factory)
        return factory

    async def _register_child_lease(self, lease: ChildRuntimeLease) -> None:
        async with self._lock:
            if not self.accepting:
                raise RuntimeError("Daytona runtime closed during child acquisition")
            record = self._child_cleanup_records.get(lease.sandbox_id)
            if record is None:
                raise RuntimeError("child Sandbox resource record is unavailable")
            if record.lease is not None and record.lease is not lease:
                raise RuntimeError("child Sandbox resource record already owns a lease")
            record.lease = lease
