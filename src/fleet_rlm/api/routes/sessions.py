"""Sessions (configuration, models, mode, history, Turn status, context, reload), memory, and Skills."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm.api.deps import Registry, Runtime, Settings, StoreDep, translate
from fleet_rlm.api.schemas import (
    Catalog,
    ContextRequest,
    ContextSelected,
    Deleted,
    Exported,
    History,
    Memories,
    MemoryRequest,
    MemorySaved,
    Mode,
    ModelRequest,
    Models,
    ModelSelection,
    ModeRequest,
    Pushed,
    SessionConfig,
    Sessions,
    Status,
    TurnStatus,
)
from fleet_rlm.optimize.artifacts import verify_active
from fleet_rlm.session import SessionRuntime, runtime_configuration
from fleet_rlm.workspace import WorkspaceDataset

router = APIRouter(tags=["sessions"])
memory_router = APIRouter(tags=["memory"])
skills_router = APIRouter(tags=["skills"])


@router.get("/sessions", response_model=Sessions)
def list_sessions(store: StoreDep) -> dict:
    with translate("Session storage is unavailable"):
        return {"sessions": store.list_sessions()}


@router.get("/models", response_model=Models)
def list_models(settings: Settings, scope: str = "system.ai") -> dict:
    invalid = "Invalid discovery scope or missing Gateway credentials"
    with translate("Gateway model discovery is unavailable", invalid=invalid):
        return {"scope": scope, "models": settings.list_models(scope)}


@router.get("/sessions/{session_id}/config", response_model=SessionConfig)
def session_config(runtime: Runtime, settings: Settings) -> dict:
    manager = runtime.sandbox_mgr
    return {
        "mode": runtime.execution_mode,
        "models": {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model},
        "context": asdict(runtime.context) if runtime.context else None,
        "configuration": runtime_configuration(settings, runtime),
        "limits": {
            "lm_calls": manager.max_llm_calls,
            "depth": manager.max_depth,
            "children": manager.max_children,
            "active_children": manager.max_active_children,
            "timeout_seconds": manager.turn_timeout_seconds,
        },
    }


@router.put("/sessions/{session_id}/model", response_model=ModelSelection)
def select_model(body: ModelRequest, runtime: Runtime, registry: Registry, settings: Settings) -> dict:
    unavailable = "Model selection is unavailable; configuration unchanged"
    with translate(unavailable, invalid="Invalid session or model configuration"):
        scope = ".".join(body.model.removeprefix("openai/").split(".")[:2])
        if body.model not in settings.list_models(scope):
            raise HTTPException(status_code=422, detail="Model is not an accessible chat service")
        if not runtime.turn_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Wait for the active turn before changing models")
        try:
            models = {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model, body.role: body.model}
            engine = registry.engine_for_models(runtime, models)
            runtime.harness.save_runtime_settings({"models": models})
            runtime.set_engine(engine)
        finally:
            runtime.turn_lock.release()
    return {"role": body.role, "model": body.model}


@router.get("/sessions/{session_id}/mode", response_model=Mode)
def get_mode(runtime: Runtime) -> dict:
    return {"mode": runtime.execution_mode}


@router.put("/sessions/{session_id}/mode", response_model=Mode)
def set_mode(body: ModeRequest, runtime: Runtime) -> dict:
    with translate("Session runtime is unavailable", invalid="Invalid Session ID or mode"), runtime.turn_lock:
        context = asdict(runtime.context) if runtime.context else None
        runtime.harness.save_runtime_settings({"mode": body.mode, "context": context})
        runtime.set_mode(body.mode)
    return {"mode": body.mode}


@router.get("/sessions/{session_id}/history", response_model=History)
def get_history(session_id: str, runtime: Runtime) -> dict:
    with translate("Session history is unavailable", invalid="Invalid Session ID"):
        return {"session_id": session_id, "turns": runtime.harness.store.history(session_id)}


@router.get("/sessions/{session_id}/turns/{turn_id}", response_model=TurnStatus)
def turn_status(turn_id: str, runtime: Runtime) -> dict:
    with translate("Turn status is unavailable", invalid="Invalid session or turn ID"):
        result = runtime.harness.turn_status(turn_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Turn was not found")
    return result


@router.put("/sessions/{session_id}/context", response_model=ContextSelected)
def select_context(session_id: str, body: ContextRequest, runtime: Runtime) -> dict:
    with translate("Context selection is unavailable", invalid="Invalid context selection"), runtime.turn_lock:
        runtime.harness.hydrate_volume_from_postgres(session_id)
        content = runtime.sandbox_mgr.read_workspace_file(body.path)
        context = WorkspaceDataset(
            workspace_path=body.path,
            description=body.description,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        runtime.harness.save_runtime_settings({"mode": runtime.execution_mode, "context": asdict(context)})
        runtime.context = context
    return {"path": body.path, "size_bytes": len(content), "sha256": context.sha256}


@router.delete("/sessions/{session_id}/context", response_model=Status)
def clear_context(runtime: Runtime) -> dict:
    with translate("Context selection is unavailable"), runtime.turn_lock:
        runtime.harness.save_runtime_settings({"mode": runtime.execution_mode, "context": None})
        runtime.context = None
    return {"status": "cleared"}


@router.post("/sessions/{session_id}/reload", response_model=Status)
def reload_program(session_id: str, runtime: Runtime, settings: Settings, approved: bool = False) -> dict:
    if not approved:
        raise HTTPException(status_code=409, detail="Explicit approval is required to reload the saved program")
    # Only operator-owned, integrity-checked artifacts may become executable programs;
    # the model-writable workspace is not an approval boundary.
    directory = Path(settings.compiled_program_dir)
    unavailable = "No approved saved program is available"
    with translate(unavailable, invalid="Saved program is invalid", status=404), runtime.turn_lock:
        artifact = verify_active(directory / f"{session_id}.active.json", directory / "candidates")
        runtime.engine.load_compiled_program(artifact.read_bytes())
    return {"status": "reloaded"}


@memory_router.get("/sessions/{session_id}/memory", response_model=Memories)
def list_memory(runtime: Runtime, q: str = "") -> dict:
    with translate("Session memory is unavailable"):
        return {"memories": runtime.harness.memories(q)}


@memory_router.put("/sessions/{session_id}/memory/{memory_id}", response_model=MemorySaved)
def put_memory(memory_id: str, body: MemoryRequest, runtime: Runtime) -> dict:
    with translate("Session memory is unavailable", invalid="Invalid memory"), runtime.turn_lock:
        runtime.harness.save_memory(
            memory_id, body.content, sources=body.sources, kind=body.kind, reviewed=body.reviewed
        )
    return {"memory_id": memory_id}


@memory_router.delete("/sessions/{session_id}/memory/{memory_id}", response_model=Deleted)
def remove_memory(memory_id: str, runtime: Runtime) -> dict:
    with translate("Session memory is unavailable", invalid="Invalid memory ID"), runtime.turn_lock:
        runtime.harness.delete_memory(memory_id)
    return {"deleted": memory_id}


def skills_runtime(registry: Registry, session_id: str = "default") -> SessionRuntime:
    with translate("Session runtime is unavailable", invalid="Invalid Session ID"):
        return registry.get(session_id)


SkillsRuntime = Annotated[SessionRuntime, Depends(skills_runtime)]


@skills_router.get("/skills", response_model=Catalog)
def get_skills_catalog(runtime: SkillsRuntime) -> dict:
    with translate("Session Skill catalog is unavailable"), runtime.turn_lock:
        runtime.skill_mgr.sync_local_skills_to_volume()
        return {"catalog_xml": runtime.skill_mgr.build_catalog_xml()}


@skills_router.post("/skills/pull", response_model=Exported)
def pull_skills_to_local(runtime: SkillsRuntime) -> dict:
    with translate("Skill export is unavailable"), runtime.turn_lock:
        return {"exported": runtime.skill_mgr.export_volume_skills_to_local()}


@skills_router.post("/skills/push", response_model=Pushed)
def push_skills(runtime: SkillsRuntime) -> dict:
    with translate("Skill push is unavailable"), runtime.turn_lock:
        return {"pushed": int(runtime.skill_mgr.sync_local_skills_to_volume())}
