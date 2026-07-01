"""Daytona SDK operations layer.

Combines volume CRUD, snapshot CRUD, and sandbox get/fork/resume into a single
thin SDK operations module.  Previously these lived in three separate files:
``volume_runtime``, ``snapshot_runtime``, and ``sandbox_lifecycle``.

This file now acts as a backward-compatible re-export barrel.  The actual
implementations live in:
- ``volumes.py``   — volume readiness, layout, listing
- ``snapshots.py`` — snapshot CRUD, bootstrap, resolution
- ``file_browser.py`` — volume tree listing, file preview
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .config import format_daytona_sdk_error as _format_daytona_sdk_error
from .errors import DaytonaDiagnosticError
from .file_browser import (
    alist_daytona_volume_tree,
    aread_daytona_volume_file_text,
)
from .snapshots import (
    DEFAULT_SNAPSHOT_BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_PACKAGES,
    bootstrap_snapshot,
    build_base_snapshot_image,
    create_sandbox_snapshot,
    create_snapshot,
    delete_snapshot,
    fallback_to_declarative_image,
    get_snapshot,
    list_snapshots,
    resolve_default_snapshot,
    resolve_sandbox_spec_snapshot,
    resolve_snapshot,
)
from .volumes import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    aensure_daytona_volume_layout,
    aensure_remote_directory,
    alist_daytona_volumes,
    await_volume_ready,
    canonicalize_volume_state_token,
    ensure_daytona_volume_layout,
    ensure_remote_directory,  # noqa: F401 — re-export for isolation.py and _git_helpers.py
    raise_if_volume_error,
    volume_state_details,
    volume_state_missing,
)

# Backward-compat sync aliases for snapshot functions.
# runtime.py imports these a* names and calls them from sync methods.
# The canonical async versions live in snapshots.py (proper async def).
acreate_snapshot = create_snapshot
adelete_snapshot = delete_snapshot
aget_snapshot = get_snapshot
alist_snapshots = list_snapshots
aresolve_snapshot = resolve_snapshot
aresolve_sandbox_spec_snapshot = resolve_sandbox_spec_snapshot
abootstrap_snapshot = bootstrap_snapshot
acreate_sandbox_snapshot = create_sandbox_snapshot

if TYPE_CHECKING:
    from .protocols import DaytonaSandbox


# ---------------------------------------------------------------------------
# Sandbox lifecycle operations  (remains here)
# ---------------------------------------------------------------------------


def _experimental_call(
    sandbox: DaytonaSandbox,
    method_name: str,
    *args: Any,
    category: str = "sandbox_experimental_error",
    phase: str = "sandbox_experimental",
    **kwargs: Any,
) -> Any:
    """Safely invoke an experimental Daytona SDK method on *sandbox*."""
    try:
        method = getattr(sandbox, method_name)
        return method(*args, **kwargs)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


def get_sandbox(
    *,
    runtime: Any,
    sandbox_id: str,
    recover: bool = True,
) -> Any:
    """Get an existing sandbox by ID, recovering from archive if needed."""
    try:
        client = runtime._get_client()
        sandbox = client.get(sandbox_id)
        if recover:
            state = getattr(sandbox, "state", None)
            state_value = getattr(state, "value", str(state or ""))
            if str(state_value).lower() in ("archived", "stopped"):
                sandbox.recover(timeout=60)
        return sandbox
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona sandbox resume failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_resume_error",
            phase="sandbox_resume",
        ) from exc


aget_sandbox = get_sandbox


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
    resumed_started = time.perf_counter()
    sandbox = get_sandbox(
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
        ensure_daytona_volume_layout(
            sandbox=sandbox,
            mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        )
    return session


aresume_workspace_session = resume_workspace_session


def fork_sandbox(
    *,
    runtime: Any,
    session: Any,
    name: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Fork a sandbox session, creating a copy-on-write clone."""
    forked = _experimental_call(
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


afork_sandbox = fork_sandbox


# ---------------------------------------------------------------------------
# Combined __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Volume (async-only public API)
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "aensure_daytona_volume_layout",
    "aensure_remote_directory",
    "alist_daytona_volume_tree",
    "alist_daytona_volumes",
    "aread_daytona_volume_file_text",
    "await_volume_ready",
    "canonicalize_volume_state_token",
    "raise_if_volume_error",
    "volume_state_details",
    "volume_state_missing",
    # Snapshot (async-only public API)
    "DEFAULT_SNAPSHOT_BASE_IMAGE",
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "abootstrap_snapshot",
    "acreate_sandbox_snapshot",
    "acreate_snapshot",
    "adelete_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_sandbox_spec_snapshot",
    "aresolve_snapshot",
    "build_base_snapshot_image",
    "fallback_to_declarative_image",
    "resolve_default_snapshot",
    # File browser (async-only public API)
    "alist_daytona_volume_tree",
    "aread_daytona_volume_file_text",
    # Sandbox lifecycle (async-only public API)
    "afork_sandbox",
    "aget_sandbox",
    "aresume_workspace_session",
    "get_sandbox_id_from_interpreter",
]


def get_sandbox_id_from_interpreter(interpreter: Any) -> str:
    """Extract the Daytona sandbox ID from a DaytonaInterpreter or session instance."""
    return (
        getattr(interpreter, "_persisted_sandbox_id", None)
        or getattr(getattr(interpreter, "session", None), "sandbox_id", None)
        or getattr(interpreter, "sandbox_id", "")
        or ""
    )
