"""Live FastAPI composition inventory (B9).

Importing this module must not require credentials or construct clients.
Construction happens only via ``install_live_composition`` when live mode is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fleet_rlm_clean.config import Settings


class LiveCompositionError(RuntimeError):
    """Raised when live composition cannot be assembled (fail closed)."""


def require_live_settings(settings: Settings) -> None:
    """Fail closed when required live deps are missing. Credentials alone are not enough."""
    if not settings.live_kernel:
        raise LiveCompositionError("live composition requires live_kernel=True")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_CLEAN_DAYTONA_API_KEY")
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value().strip():
        missing.append("FLEET_CLEAN_LLM_API_KEY")
    if not (settings.database_url or "").strip():
        missing.append("FLEET_CLEAN_DATABASE_URL")
    if settings.auth_mode == "neon" and not (settings.neon_auth_url or "").strip():
        missing.append("FLEET_CLEAN_NEON_AUTH_URL")
    if missing:
        raise LiveCompositionError("live composition missing required settings: " + ", ".join(missing))


@dataclass(slots=True)
class LiveCompositionHandles:
    """Process-owned live handles disposed on shutdown."""

    resources: Any
    turn_coordinator: Any
    session_repository: Any
    attachment_store: Any
    artifact_store: Any
    workspace_volume_mirror: Any


def _host_roots(settings: Settings) -> tuple[str, str]:
    upload_root = settings.upload_root or str(Path.cwd() / ".fleet_clean_uploads")
    if settings.artifact_root:
        artifact_root = settings.artifact_root
    else:
        artifact_root = str(Path(upload_root).parent / "artifacts")
    return upload_root, artifact_root


def build_live_composition(settings: Settings) -> LiveCompositionHandles:
    """Construct the live lifespan inventory (sync SDK/LM construction)."""
    require_live_settings(settings)

    from fleet_rlm_clean.artifacts.store import LocalArtifactStore
    from fleet_rlm_clean.chat.live_context import LiveKernelResources, settings_with_env_fallbacks
    from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm_clean.daytona.paths import volume_paths_from_settings
    from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
    from fleet_rlm_clean.files.uploads import LocalAttachmentStore
    from fleet_rlm_clean.observability.exporters import LoggingTurnExporter
    from fleet_rlm_clean.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
    )
    from fleet_rlm_clean.rlm.factory import RLMFactory
    from fleet_rlm_clean.rlm.runner import RLMRunner

    resolved = settings_with_env_fallbacks(settings)
    require_live_settings(resolved.model_copy(update={"live_kernel": True}))

    engine = create_async_engine_from_url(resolved.database_url or "")
    session_factory = create_session_factory(engine)
    resources = LiveKernelResources(
        resolved,
        session_factory=session_factory,
        engine=engine,
        allow_ephemeral_fallback=False,
    )
    assert resources.sessions is not None

    upload_root, artifact_root = _host_roots(resolved)
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(resolved),
    )
    attachment_store = LocalAttachmentStore(
        upload_root,
        max_bytes=resolved.max_upload_bytes,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    artifact_store = LocalArtifactStore(
        artifact_root,
        max_bytes=resolved.max_artifact_bytes,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    resources.attachment_store = attachment_store
    resources.artifact_store = artifact_store

    runner = RLMRunner(factory=RLMFactory())
    coordinator = TurnCoordinator(
        runner=runner,
        context_builder=resources,
        session_repository=resources.sessions,
    )
    # Optional non-fatal exporter (attach for callers; coordinator may ignore).
    _ = LoggingTurnExporter()

    return LiveCompositionHandles(
        resources=resources,
        turn_coordinator=coordinator,
        session_repository=resources.sessions,
        attachment_store=attachment_store,
        artifact_store=artifact_store,
        workspace_volume_mirror=mirror,
    )


async def install_live_composition(app: FastAPI, settings: Settings) -> LiveCompositionHandles:
    """Attach live inventory to ``app.state``; create tables; seed skill hosts on resources."""
    from fleet_rlm_clean.persistence.database import create_tables

    handles = build_live_composition(settings)
    await create_tables(handles.resources._engine)  # noqa: SLF001

    skill_registry = getattr(app.state, "skill_registry", None)
    handles.resources.skill_registry = skill_registry

    app.state.live_mode = True
    app.state.live_kernel_resources = handles.resources
    app.state.db_engine = handles.resources._engine  # noqa: SLF001
    app.state.session_repository = handles.session_repository
    app.state.turn_coordinator = handles.turn_coordinator
    app.state.attachment_store = handles.attachment_store
    app.state.artifact_store = handles.artifact_store
    app.state.workspace_volume_mirror = handles.workspace_volume_mirror
    app.state.session_manager = handles.resources.session_manager
    app.state.rlm_model_bundle = handles.resources.models

    if settings.auth_mode == "neon":
        from fleet_rlm_clean.api.neon_auth import NeonAuthVerifier

        app.state.auth_verifier = NeonAuthVerifier(neon_auth_url=settings.neon_auth_url or "")

    return handles


async def dispose_live_composition(app: FastAPI) -> None:
    """Best-effort shutdown of live resources."""
    resources = getattr(app.state, "live_kernel_resources", None)
    if resources is not None:
        dispose = getattr(resources, "adispose", None)
        if callable(dispose):
            await dispose()
    app.state.live_mode = False


def is_live_mode(app: FastAPI | Any) -> bool:
    state = getattr(app, "state", app)
    if getattr(state, "live_mode", False):
        return True
    settings = getattr(state, "settings", None)
    return bool(getattr(settings, "live_kernel", False))


__all__ = [
    "LiveCompositionError",
    "LiveCompositionHandles",
    "build_live_composition",
    "dispose_live_composition",
    "install_live_composition",
    "is_live_mode",
    "require_live_settings",
]
