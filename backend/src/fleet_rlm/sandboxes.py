"""Session sandbox binding, workspace hydration, and durable object cleanup."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from . import db as store
from .config import Settings
from .interpreter import (
    AsyncSandbox,
    DaytonaError,
    SandboxStartupError,
    allocate_session_sandbox,
    delete_sandbox_by_id,
    get_running_sandbox,
    prepare_sandbox,
    reset_managed_workspace,
    reusable_session_sandbox,
    upload_workspace_file,
)
from .runtime import Runtime, active_session, effective_settings
from .storage import StorageError

_LOG = logging.getLogger(__name__)


def safe_component(name: str) -> bool:
    return (
        bool(name)
        and name == name.strip()
        and name not in {".", ".."}
        and not any(char in name for char in "/\\:%")
        and name.isprintable()
        and len(name.encode()) <= 255
    )


def valid_file_path(path: str) -> bool:
    root, *parts = path.split("/")
    return root in {"files", "artifacts"} and bool(parts) and all(map(safe_component, parts))


def browsable_workspace_path(path: str) -> bool:
    parts = path.split("/")
    return parts[0] != "artifacts" and ".git" not in parts and all(map(safe_component, parts))


async def ensure_bound_sandbox(
    runtime: Runtime,
    effective: Settings,
    session_id: UUID,
    existing_sandbox_id: str | None,
    *,
    owner_run_id: UUID,
    needs_cleanup: bool = False,
) -> AsyncSandbox:
    if existing_sandbox_id:
        if needs_cleanup:
            try:
                await delete_sandbox_by_id(runtime.daytona, existing_sandbox_id)
            except DaytonaError as error:
                raise SandboxStartupError(
                    "Could not retire the previous Daytona workspace"
                ) from error
        elif sandbox := await reusable_session_sandbox(runtime.daytona, existing_sandbox_id):
            if sandbox.state != "started":
                await prepare_sandbox(sandbox)
            return sandbox
    name, recovering = await store.reserve_session_sandbox(
        runtime.sessionmaker, session_id, owner_run_id, expected=existing_sandbox_id
    )
    sandbox = await allocate_session_sandbox(
        runtime.daytona, effective, name, recovering=recovering
    )
    # A stale worker leaves the reservation for the next owner to reconcile.
    _, won = await store.rebind_session_sandbox(
        runtime.sessionmaker,
        session_id,
        sandbox.id,
        expected=existing_sandbox_id,
        owner_run_id=owner_run_id,
        allocation_name=name,
    )
    if not won:
        raise SandboxStartupError("The Daytona workspace binding changed during startup")
    await prepare_sandbox(sandbox)
    return sandbox


async def chat_sandbox(
    runtime: Runtime,
    session: store.Session,
    settings: Settings | None = None,
    *,
    owner_run_id: UUID,
) -> AsyncSandbox:
    effective = settings if settings is not None else await effective_settings(runtime)
    return await ensure_bound_sandbox(
        runtime,
        effective,
        session.id,
        session.daytona_sandbox_id,
        owner_run_id=owner_run_id,
        needs_cleanup=session.sandbox_needs_cleanup,
    )


async def browse_sandbox(runtime: Runtime, session_id: UUID) -> tuple[AsyncSandbox | None, int]:
    async with runtime.sessionmaker() as db:
        session = await active_session(db, session_id)
        sandbox_id = session.daytona_sandbox_id
    if sandbox_id is None or session.sandbox_needs_cleanup:
        return None, 0
    effective = await effective_settings(runtime)
    try:
        sandbox = await get_running_sandbox(runtime.daytona, sandbox_id)
    except DaytonaError:
        sandbox = None
    return sandbox, effective.daytona_exec_timeout_seconds


def file_manifest(file: store.File) -> dict:
    return {
        "path": file.path,
        "kind": file.kind,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
    }


async def hydrate_workspace(
    runtime: Runtime, sandbox: AsyncSandbox, files: list[store.File], timeout: int
) -> None:
    await reset_managed_workspace(sandbox, timeout=timeout)
    with TemporaryDirectory(prefix="fleet-hydrate-") as temporary:
        path = Path(temporary) / "file"
        for file in files:
            if not valid_file_path(file.path):
                raise StorageError("Invalid durable workspace path")
            await runtime.storage.download(
                file.object_key, path, sha256=file.sha256, size=file.size_bytes
            )
            await upload_workspace_file(
                sandbox, path, file.path, sha256=file.sha256, size=file.size_bytes, timeout=timeout
            )


async def retire_workspace(runtime: Runtime, sandbox_id: str) -> None:
    try:
        await delete_sandbox_by_id(runtime.daytona, sandbox_id)
    except DaytonaError as error:
        _LOG.warning("Could not retire Daytona VM %s (%s)", sandbox_id, type(error).__name__)


async def reclaim_discarded_objects(runtime: Runtime, session_id: UUID) -> None:
    reclaimed = []
    try:
        for entry in await store.list_discarded_objects(runtime.sessionmaker, session_id):
            await runtime.storage.reclaim(entry.object_key, entry.upload_id)
            reclaimed.append(entry.object_key)
    finally:
        if reclaimed:
            await store.forget_discarded_objects(runtime.sessionmaker, reclaimed)


async def try_object_cleanup(runtime: Runtime, session_id: UUID) -> None:
    try:
        await reclaim_discarded_objects(runtime, session_id)
    except (StorageError, SQLAlchemyError) as error:
        _LOG.warning("Workspace object cleanup will retry (%s)", type(error).__name__)
