"""Sandbox lifecycle helpers extracted from the Daytona runtime."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from .async_compat import _await_if_needed, _run_async_compat
from .config import format_daytona_sdk_error as _format_daytona_sdk_error
from .diagnostics import DaytonaDiagnosticError
from .volume_runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    aensure_daytona_volume_layout as _aensure_daytona_volume_layout,
)


async def _experimental_call(
    sandbox: Any,
    method_name: str,
    *args: Any,
    category: str = "sandbox_experimental_error",
    phase: str = "sandbox_experimental",
    **kwargs: Any,
) -> Any:
    """Safely invoke an experimental Daytona SDK method on *sandbox*."""
    try:
        method = getattr(sandbox, method_name)
        return await _await_if_needed(method(*args, **kwargs))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


async def adelete_sandbox_session(session: Any) -> None:
    await session.adelete_context()
    # Graceful stop before delete to let processes flush/clean up.
    with suppress(Exception):
        await _await_if_needed(session.sandbox.stop(timeout=10))
    with suppress(Exception):
        await _await_if_needed(session.sandbox.delete())
    session._driver_started = False


def delete_sandbox_session(session: Any) -> None:
    _run_async_compat(adelete_sandbox_session, session)


async def aarchive_sandbox_session(session: Any) -> None:
    """Archive the sandbox for later recovery (cheaper than keeping it running)."""
    await _await_if_needed(session.sandbox.archive())


def archive_sandbox_session(session: Any) -> None:
    _run_async_compat(aarchive_sandbox_session, session)


async def arecover_sandbox_session(session: Any, *, timeout: float = 60.0) -> None:
    """Recover an archived sandbox, restoring it to a running state."""
    await _await_if_needed(session.sandbox.recover(timeout=timeout))


def recover_sandbox_session(session: Any, *, timeout: float = 60.0) -> None:
    _run_async_compat(arecover_sandbox_session, session, timeout=timeout)


async def arefresh_sandbox_session_activity(session: Any) -> None:
    """Reset the sandbox auto-stop timer without changing state."""
    with suppress(Exception):
        await _await_if_needed(session.sandbox.refresh_activity())


def refresh_sandbox_session_activity(session: Any) -> None:
    _run_async_compat(arefresh_sandbox_session_activity, session)


async def aresize_sandbox_session(
    session: Any,
    *,
    cpu: int,
    memory: int,
    disk: int,
) -> None:
    """Hot-resize sandbox resources (CPU cores, memory GiB, disk GiB)."""
    from daytona import Resources

    await _await_if_needed(
        session.sandbox.resize(Resources(cpu=cpu, memory=memory, disk=disk))
    )


def resize_sandbox_session(
    session: Any,
    *,
    cpu: int,
    memory: int,
    disk: int,
) -> None:
    _run_async_compat(
        aresize_sandbox_session,
        session,
        cpu=cpu,
        memory=memory,
        disk=disk,
    )


def create_lsp_server(
    session: Any,
    *,
    language: str = "python",
    project_path: str | None = None,
) -> Any:
    """Create a Daytona LSP server for code intelligence in the sandbox."""
    path = project_path or session.workspace_path
    return session.sandbox.create_lsp_server(language, path)


async def aget_sandbox(
    *,
    runtime: Any,
    sandbox_id: str,
    recover: bool = True,
) -> Any:
    """Get an existing sandbox by ID, recovering from archive if needed."""
    try:
        client = await runtime._aget_client()
        sandbox = await _await_if_needed(client.get(sandbox_id))
        if recover:
            state = getattr(sandbox, "state", None)
            state_value = getattr(state, "value", str(state or ""))
            if str(state_value).lower() in ("archived", "stopped"):
                await _await_if_needed(sandbox.recover(timeout=60))
        return sandbox
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona sandbox resume failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_resume_error",
            phase="sandbox_resume",
        ) from exc


async def aresume_workspace_session(
    *,
    runtime: Any,
    sandbox_id: str,
    repo_url: str | None,
    ref: str | None,
    volume_name: str | None = None,
    workspace_path: str,
    context_sources: list[Any] | None = None,
    context_id: str | None = None,
) -> Any:
    resumed_started = time.perf_counter()
    sandbox = await aget_sandbox(
        runtime=runtime,
        sandbox_id=sandbox_id,
    )
    session = runtime._build_workspace_session(
        sandbox=sandbox,
        repo_url=repo_url,
        resolved_ref=ref,
        volume_name=volume_name,
        workspace_path=workspace_path,
        context_sources=list(context_sources or []),
        timings={"sandbox_resume": int((time.perf_counter() - resumed_started) * 1000)},
        context_id=context_id,
    )
    if volume_name:
        await _aensure_daytona_volume_layout(
            sandbox=sandbox,
            mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        )
    return session


def resume_workspace_session(
    *,
    runtime: Any,
    sandbox_id: str,
    repo_url: str | None,
    ref: str | None,
    volume_name: str | None = None,
    workspace_path: str,
    context_sources: list[Any] | None = None,
    context_id: str | None = None,
) -> Any:
    return _run_async_compat(
        aresume_workspace_session,
        runtime=runtime,
        sandbox_id=sandbox_id,
        repo_url=repo_url,
        ref=ref,
        volume_name=volume_name,
        workspace_path=workspace_path,
        context_sources=context_sources,
        context_id=context_id,
    )


async def afork_sandbox(
    *,
    runtime: Any,
    session: Any,
    name: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Fork a sandbox session, creating a copy-on-write clone."""
    forked = await _experimental_call(
        session.sandbox,
        "_experimental_fork",
        name=name,
        timeout=timeout,
        category="sandbox_fork_error",
        phase="sandbox_fork",
    )
    return runtime._build_workspace_session(
        sandbox=forked,
        repo_url=session.repo_url,
        resolved_ref=session.ref,
        volume_name=session.volume_name,
        workspace_path=session.workspace_path,
        context_sources=list(session.context_sources),
        timings={"sandbox_fork": 0},
    )


def fork_sandbox(
    *,
    runtime: Any,
    session: Any,
    name: str | None = None,
    timeout: float = 60.0,
) -> Any:
    return _run_async_compat(
        afork_sandbox,
        runtime=runtime,
        session=session,
        name=name,
        timeout=timeout,
    )


__all__ = [
    "aarchive_sandbox_session",
    "adelete_sandbox_session",
    "afork_sandbox",
    "aget_sandbox",
    "arecover_sandbox_session",
    "arefresh_sandbox_session_activity",
    "aresize_sandbox_session",
    "aresume_workspace_session",
    "archive_sandbox_session",
    "create_lsp_server",
    "delete_sandbox_session",
    "fork_sandbox",
    "recover_sandbox_session",
    "refresh_sandbox_session_activity",
    "resize_sandbox_session",
]
