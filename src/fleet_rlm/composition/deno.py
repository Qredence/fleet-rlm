"""Reduced local Deno/Pyodide runtime composition."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fleet_rlm.composition.common import (
    CompositionError,
    LocalCompositionHandles,
    build_local_storage_adapters,
    host_roots,
    install_local_inventory,
    recursive_rlm_options,
    rlm_options,
)
from fleet_rlm.config import Settings


def require_deno_settings(settings: Settings) -> None:
    """
    Validate that the settings and local dependencies required for Deno mode are available.
    
    Parameters:
        settings (Settings): Runtime settings to validate.
    
    Raises:
        CompositionError: If Deno mode is not selected, no provider API key is configured, or the `deno` executable is unavailable.
    """
    if settings.run_environment != "deno":
        raise CompositionError("Deno composition requires run_environment='deno'")
    from fleet_rlm.rlm.lm_factory import has_llm_credentials

    if not has_llm_credentials(settings):
        raise CompositionError("a configured provider API key is required in deno mode")
    if shutil.which("deno") is None:
        raise CompositionError("deno executable is required in deno mode")


def install_deno_composition(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> LocalCompositionHandles:
    """
    Install the Deno runtime composition on the application.
    
    Parameters:
        session_factory (Any | None): Optional database session factory used to enable SQL-backed artifact storage.
    
    Returns:
        LocalCompositionHandles: Handles for the installed local composition.
    """
    from fleet_rlm.artifacts.local_catalog import LocalArtifactBlobGateway, LocalArtifactCatalog
    from fleet_rlm.chat.deno_run_environment import DenoTurnPreparation
    from fleet_rlm.files.local_catalog import LocalAttachmentBlobGateway
    from fleet_rlm.files.paths import LocalAttachmentPathPolicy
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.lm_factory import build_model_bundle

    models = build_model_bundle(settings)
    upload_root, artifact_root = host_roots(settings)
    sql_artifact_blobs = None
    if session_factory is not None:
        artifact_catalog = LocalArtifactCatalog(
            artifact_root,
            max_bytes=settings.max_artifact_bytes,
            volume_paths=None,
        )
        sql_artifact_blobs = LocalArtifactBlobGateway(artifact_catalog)
    storage = build_local_storage_adapters(
        settings,
        session_factory=session_factory,
        volume_paths=None,
        sql_attachment_blobs=LocalAttachmentBlobGateway(Path(upload_root)),
        sql_attachment_paths=LocalAttachmentPathPolicy(Path(upload_root)),
        sql_artifact_blobs=sql_artifact_blobs,
    )
    return install_local_inventory(
        app,
        settings,
        session_factory=session_factory,
        attachment_lifecycle=storage.attachment_lifecycle,
        artifact_reader=storage.artifact_reader,
        preparation=DenoTurnPreparation(
            attachments=storage.attachment_lifecycle,
            options=rlm_options(settings),
            recursive_options=recursive_rlm_options(settings),
            root_lm=models.root_lm,
            sub_lm=models.sub_lm,
            skill_catalog=app.state.skill_catalog,
            max_artifact_bytes=settings.max_artifact_bytes,
            max_url_bytes=settings.max_upload_bytes,
        ),
        rlm_factory=RLMFactory(verbose=settings.rlm_verbose),
        workspace_volume_mirror=None,
    )
