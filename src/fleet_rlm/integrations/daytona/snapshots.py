"""Daytona snapshot operations — CRUD, bootstrap, resolution, and fallback."""

from __future__ import annotations

import dataclasses
import logging
import re
import shlex
import time as _time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from .async_compat import _run_sync_in_thread
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import (
    build_daytona_client as _build_daytona_client,
)
from .config import (
    classify_daytona_sdk_error as _classify_daytona_sdk_error,
)
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .config import format_daytona_sdk_error as _format_daytona_sdk_error
from .errors import DaytonaDiagnosticError
from .models import SandboxSpec

if TYPE_CHECKING:
    from .protocols import DaytonaSandbox

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_PACKAGES: list[str] = [
    "dspy==3.3.0b1",
    "numpy",
    "pandas",
    "httpx",
    "pydantic",
]

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-base"
DEFAULT_SNAPSHOT_BASE_IMAGE = "python:3.12-slim"
_VALID_PACKAGE_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],<>=!~]*$")


def canonicalize_snapshot_state_token(value: Any) -> str:
    """Normalize raw Daytona SDK snapshot state values to a lowercase token.

    Mirrors ``volumes.canonicalize_volume_state_token`` so snapshot state
    checks survive SDK enum stringification changes (``SnapshotState.ACTIVE``
    vs ``ACTIVE`` vs ``SnapshotState.ACTIVE`` etc.).
    """
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    else:
        state_value = getattr(value, "value", None)
        if state_value not in (None, ""):
            candidates.append(str(state_value))
        state_name = getattr(value, "name", None)
        if state_name not in (None, ""):
            candidates.append(str(state_name))
        if value not in (None, ""):
            candidates.append(str(value))
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        normalized = normalized.replace("-", "_").replace(" ", "_")
        if "." in normalized:
            normalized = normalized.rsplit(".", 1)[-1]
        if normalized:
            return normalized
    return ""


BROWSER_SNAPSHOT_NAME = "fleet-rlm-browser"
BROWSER_SNAPSHOT_PACKAGES: list[str] = [
    *DEFAULT_SNAPSHOT_PACKAGES,
    "playwright",
]

_CHROMIUM_SYSTEM_DEPS = (
    "libx11-6 libxrandr2 libxext6 libxrender1 libxfixes3 libxss1 "
    "libxtst6 libxi6 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 "
    "libcups2 libdrm2 libgbm1 libpango-1.0-0 libcairo2 libasound2 "
    "libatspi2.0-0 libdbus-1-3 fonts-liberation"
)
_VNC_DESKTOP_DEPS = "xvfb xfce4 xfce4-terminal x11vnc novnc dbus-x11"


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


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "name": snapshot.name,
        "id": snapshot.id,
        "state": str(getattr(snapshot, "state", "unknown")),
        "image_name": getattr(snapshot, "image_name", None),
    }


def build_base_snapshot_image(
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
) -> Any:
    """Build the default Daytona declarative image used by snapshots and fallback sandboxes."""
    try:
        from daytona import Image as DaytonaImage
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    packages_to_install = packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES
    for package in packages_to_install:
        if not package or not _VALID_PACKAGE_SPEC_PATTERN.fullmatch(package):
            msg = f"Invalid package spec for snapshot image install: {package!r}"
            raise ValueError(msg)

    image = DaytonaImage.base(base_image).run_commands("pip install uv")
    if packages_to_install:
        install_command = shlex.join(["uv", "pip", "install", "--system", *packages_to_install])
        image = image.run_commands(install_command)
    return image


def build_browser_snapshot_image(
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    include_vnc: bool = True,
) -> Any:
    """Build a Daytona image with Playwright, Chromium, and optional VNC/desktop."""
    try:
        from daytona import Image as DaytonaImage
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    packages_to_install = packages if packages is not None else BROWSER_SNAPSHOT_PACKAGES
    for package in packages_to_install:
        if not package or not _VALID_PACKAGE_SPEC_PATTERN.fullmatch(package):
            msg = f"Invalid package spec for browser snapshot image install: {package!r}"
            raise ValueError(msg)

    system_deps = _CHROMIUM_SYSTEM_DEPS
    if include_vnc:
        system_deps += f" {_VNC_DESKTOP_DEPS}"

    image = DaytonaImage.base(base_image).run_commands(
        f"apt-get update && apt-get install -y --no-install-recommends {system_deps} && rm -rf /var/lib/apt/lists/*"
    )
    image = image.run_commands("pip install uv")
    if packages_to_install:
        install_command = shlex.join(["uv", "pip", "install", "--system", *packages_to_install])
        image = image.run_commands(install_command)
    image = image.run_commands("playwright install chromium")
    return image


def create_browser_snapshot(
    name: str = BROWSER_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    include_vnc: bool = True,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a browser-capable Daytona snapshot with Playwright and Chromium."""
    try:
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = build_browser_snapshot_image(base_image=base_image, packages=packages, include_vnc=include_vnc)
    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.create(params, on_logs=on_logs, timeout=0)
        logger.info("Browser snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        with suppress(Exception):
            client.close()


async def acreate_browser_snapshot(
    name: str = BROWSER_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    include_vnc: bool = True,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        create_browser_snapshot,
        name,
        base_image=base_image,
        packages=packages,
        include_vnc=include_vnc,
        config=config,
        on_logs=on_logs,
    )


def bootstrap_browser_snapshot(
    name: str = BROWSER_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    include_vnc: bool = True,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Ensure the reusable Fleet browser Daytona snapshot exists."""
    cfg = config or resolve_daytona_config()
    existing = get_snapshot(name, config=cfg)
    if existing is not None and not refresh:
        return {**existing, "created": False, "refreshed": False}
    if existing is not None:
        previous_snapshot_id = str(existing.get("id") or "") or None
        delete_snapshot(previous_snapshot_id or str(existing.get("name") or name), config=cfg)
        replacement = _wait_for_snapshot_refresh_target(
            name,
            previous_snapshot_id=previous_snapshot_id,
            config=cfg,
        )
        if replacement is not None:
            return {**replacement, "created": False, "refreshed": True}

    try:
        created = create_browser_snapshot(
            name=name,
            base_image=base_image,
            include_vnc=include_vnc,
            config=cfg,
            on_logs=on_logs,
        )
    except Exception as exc:
        if _snapshot_create_conflict(exc):
            replacement = get_snapshot(name, config=cfg)
            if replacement is not None:
                return {**replacement, "created": False, "refreshed": existing is not None}
        raise
    return {**created, "created": True, "refreshed": existing is not None}


async def abootstrap_browser_snapshot(
    name: str = BROWSER_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    include_vnc: bool = True,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        bootstrap_browser_snapshot,
        name,
        base_image=base_image,
        refresh=refresh,
        include_vnc=include_vnc,
        config=config,
        on_logs=on_logs,
    )


def list_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a lightweight list of available snapshots."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        result = client.snapshot.list()
        items = result.items if hasattr(result, "items") else result
        return [_snapshot_summary(snapshot) for snapshot in items]
    finally:
        with suppress(Exception):
            client.close()


async def alist_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(list_snapshots, config)


def get_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Look up a snapshot by *name*, returning a summary dict or ``None``."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        return _snapshot_summary(snapshot)
    except Exception as exc:
        if _snapshot_lookup_missing(exc):
            logger.debug("snapshot_lookup_missing", extra={"name": name}, exc_info=True)
            return None
        raise DaytonaDiagnosticError(
            f"Daytona snapshot lookup failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_snapshot_error",
            phase="snapshot_lookup",
        ) from exc
    finally:
        with suppress(Exception):
            client.close()


async def aget_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(get_snapshot, name, config=config)


def _snapshot_lookup_missing(exc: BaseException) -> bool:
    classification = _classify_daytona_sdk_error(exc)
    if classification.status_code == 404:
        return True
    lowered = classification.message.lower()
    return "snapshot" in lowered and "not found" in lowered


def _snapshot_create_conflict(exc: BaseException) -> bool:
    """Return True for a genuine snapshot name collision (HTTP 400/409).

    Requires a name-collision token (``"already exists"`` or ``"snapshot"``
    alongside ``"with name"``) so a 400 for a genuinely malformed snapshot name
    containing the word "conflict" does not false-positive into the
    create-then-reuse recovery path.
    """
    classification = _classify_daytona_sdk_error(exc)
    lowered = classification.message.lower()
    if classification.status_code not in {400, 409}:
        return False
    if "already exists" in lowered:
        return True
    return "conflict" in lowered and "with name" in lowered


def _wait_for_snapshot_refresh_target(
    name: str,
    *,
    previous_snapshot_id: str | None,
    config: ResolvedDaytonaConfig,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait for a refreshed snapshot name to disappear or be replaced."""

    deadline = _time.monotonic() + timeout
    interval = 0.1

    while _time.monotonic() < deadline:
        current = get_snapshot(name, config=config)
        if current is None:
            return None
        current_id = str(current.get("id") or "") or None
        if previous_snapshot_id is None or current_id != previous_snapshot_id:
            return current
        _time.sleep(interval)
        interval = min(interval * 2, 1.0)

    raise DaytonaDiagnosticError(
        f"Timed out waiting for Daytona snapshot '{name}' to finish refreshing",
        category="sandbox_snapshot_error",
        phase="snapshot_refresh",
    )


def create_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages."""
    try:
        from daytona.common.snapshot import CreateSnapshotParams
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    image = build_base_snapshot_image(base_image=base_image, packages=packages)
    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.create(params, on_logs=on_logs, timeout=0)
        logger.info("Snapshot '%s' created (id=%s)", snapshot.name, snapshot.id)
        return _snapshot_summary(snapshot)
    finally:
        with suppress(Exception):
            client.close()


async def acreate_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        create_snapshot, name, base_image=base_image, packages=packages, config=config, on_logs=on_logs
    )


def delete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Delete a Daytona snapshot by name or id."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snapshot = client.snapshot.get(name)
        client.snapshot.delete(snapshot)
    finally:
        with suppress(Exception):
            client.close()


async def adelete_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> None:
    """Async wrapper — runs blocking SDK call in a thread."""
    await _run_sync_in_thread(delete_snapshot, name, config=config)


def bootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Ensure the reusable Fleet Daytona base snapshot exists."""
    cfg = config or resolve_daytona_config()
    existing = get_snapshot(name, config=cfg)
    if existing is not None and not refresh:
        return {**existing, "created": False, "refreshed": False}
    if existing is not None:
        previous_snapshot_id = str(existing.get("id") or "") or None
        delete_snapshot(previous_snapshot_id or str(existing.get("name") or name), config=cfg)
        replacement = _wait_for_snapshot_refresh_target(
            name,
            previous_snapshot_id=previous_snapshot_id,
            config=cfg,
        )
        if replacement is not None:
            return {**replacement, "created": False, "refreshed": True}

    try:
        created = create_snapshot(
            name=name,
            base_image=base_image,
            config=cfg,
            on_logs=on_logs,
        )
    except Exception as exc:
        if _snapshot_create_conflict(exc):
            replacement = get_snapshot(name, config=cfg)
            if replacement is not None:
                return {**replacement, "created": False, "refreshed": existing is not None}
        raise
    return {**created, "created": True, "refreshed": existing is not None}


async def abootstrap_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = DEFAULT_SNAPSHOT_BASE_IMAGE,
    refresh: bool = False,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(
        bootstrap_snapshot, name, base_image=base_image, refresh=refresh, config=config, on_logs=on_logs
    )


def resolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = get_snapshot(preferred_name, config=config)
    if not info:
        return None
    state = canonicalize_snapshot_state_token(info.get("state"))
    if state == "active":
        return info["name"]
    return None


async def aresolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(resolve_snapshot, preferred_name, config=config)


def resolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Resolve a snapshot-backed spec, falling back to the shared declarative image when needed."""
    if not spec.snapshot or spec.uses_declarative_image:
        return spec
    active_snapshot = resolve_snapshot(spec.snapshot, config=config)
    if active_snapshot is not None:
        return spec
    # Warn so operators know the snapshot is missing and a slower image build
    # is being used. For the browser snapshot this also means Playwright may
    # take a one-time ~2-5 min install (Daytona caches built images ~24h).
    logger.warning(
        "Daytona snapshot '%s' is not ACTIVE; falling back to a declarative image build. "
        "Run the snapshot bootstrap to speed up sandbox start.",
        spec.snapshot,
    )
    return fallback_to_declarative_image(spec)


async def aresolve_sandbox_spec_snapshot(
    spec: SandboxSpec,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> SandboxSpec:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(resolve_sandbox_spec_snapshot, spec, config=config)


def resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
    """Choose the runtime default snapshot when neither image nor snapshot is set."""
    if snapshot or image:
        return snapshot
    return DEFAULT_SNAPSHOT_NAME


def fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
    """Replace a snapshot-based spec with a declarative image build.

    Snapshot-aware: if the spec requested the browser snapshot, fall back to
    ``build_browser_snapshot_image`` so Playwright/Chromium remain available.
    Otherwise fall back to the base image. Without this, a missing
    ``fleet-rlm-browser`` snapshot would silently degrade to a plain Python
    sandbox where ``import playwright`` fails at runtime.
    """
    intended_snapshot = spec.snapshot
    if intended_snapshot == BROWSER_SNAPSHOT_NAME:
        image = build_browser_snapshot_image()
    else:
        image = build_base_snapshot_image()
    return dataclasses.replace(spec, image=image, snapshot=None)


def create_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Create a snapshot from the current state of a sandbox session."""
    _experimental_call(
        session.sandbox,
        "_experimental_create_snapshot",
        name=name,
        timeout=timeout,
        category="sandbox_snapshot_error",
        phase="sandbox_snapshot",
    )
    return {
        "name": name,
        "sandbox_id": getattr(session, "sandbox_id", None),
        "status": "created",
    }


async def acreate_sandbox_snapshot(
    session: Any,
    *,
    name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Async wrapper — runs blocking SDK call in a thread."""
    return await _run_sync_in_thread(create_sandbox_snapshot, session, name=name, timeout=timeout)


__all__ = [
    "BROWSER_SNAPSHOT_NAME",
    "BROWSER_SNAPSHOT_PACKAGES",
    "DEFAULT_SNAPSHOT_BASE_IMAGE",
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "abootstrap_browser_snapshot",
    "abootstrap_snapshot",
    "acreate_browser_snapshot",
    "acreate_sandbox_snapshot",
    "acreate_snapshot",
    "adelete_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_sandbox_spec_snapshot",
    "aresolve_snapshot",
    "bootstrap_browser_snapshot",
    "build_base_snapshot_image",
    "build_browser_snapshot_image",
    "canonicalize_snapshot_state_token",
    "create_browser_snapshot",
    "fallback_to_declarative_image",
    "resolve_default_snapshot",
]
