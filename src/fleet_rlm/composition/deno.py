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
    rlm_options,
)
from fleet_rlm.config import Settings


def require_deno_settings(settings: Settings) -> None:
    """Fail closed when Deno dependencies are missing."""
    if settings.run_environment != "deno":
        raise CompositionError("Deno composition requires run_environment='deno'")
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value().strip():
        raise CompositionError("FLEET_LLM_API_KEY is required in deno mode")
    if shutil.which("deno") is None:
        raise CompositionError("deno executable is required in deno mode")


def install_deno_composition(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> LocalCompositionHandles:
    """Build Deno adapters once during lifespan."""
    from fleet_rlm.artifacts.local_catalog import LocalArtifactBlobGateway, LocalArtifactCatalog
    from fleet_rlm.chat.deno_run_environment import DenoTurnPreparation
    from fleet_rlm.files.local_catalog import LocalAttachmentBlobGateway
    from fleet_rlm.files.paths import LocalAttachmentPathPolicy
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.lm_factory import build_lm

    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    root_lm = build_lm(
        settings.root_model,
        api_key=api_key,
        base_url=settings.llm_base_url,
        max_tokens=settings.llm_max_tokens,
    )
    sub_lm = build_lm(
        settings.sub_model,
        api_key=api_key,
        base_url=settings.llm_base_url,
        max_tokens=settings.llm_max_tokens,
    )
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
            turn_timeout_seconds=settings.turn_timeout_seconds,
            root_lm=root_lm,
            sub_lm=sub_lm,
            skill_catalog=app.state.skill_catalog,
            max_artifact_bytes=settings.max_artifact_bytes,
        ),
        rlm_factory=RLMFactory(),
        workspace_volume_mirror=None,
    )
