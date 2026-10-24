"""Sessions (configuration, models, mode, history, Turn status, context, reload), memory, and Skills."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.api.deps import LOCKED, Registry, Runtime, Settings, StoreDep, session_lock, translate
from fleet_rlm.api.schemas import (
    Catalog,
    ContextRequest,
    ContextSelected,
    Deleted,
    Exported,
    History,
    Limits,
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
from fleet_rlm.session import SessionRuntime, runtime_configuration
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id

router = APIRouter(tags=["sessions"])
memory_router = APIRouter(tags=["memory"])
skills_router = APIRouter(tags=["skills"])


@router.get("/sessions")
def list_sessions(store: StoreDep) -> Sessions:
    """List stored Sessions with their update times and Turn counts."""
    with translate("Session storage is unavailable"):
        return Sessions(sessions=store.list_sessions())


@router.get("/models")
def list_models(
    settings: Settings,
    scope: Annotated[str, Query(description="Runtime scope tag for model discovery")] = "system.ai",
) -> Models:
    """List the chat models in a discovery scope.

    An invalid scope or missing Gateway credentials returns 422.
    """
    invalid = "Invalid discovery scope or missing Gateway credentials"
    with translate("Gateway model discovery is unavailable", invalid=invalid):
        return Models(scope=scope, models=settings.list_models(scope))


@router.delete("/sessions/{session_id}", responses=LOCKED)
def evict_session(session_id: str, registry: Registry) -> Deleted:
    """Stop a Session's sandbox and release its runtime; stored history is kept.

    Returns 404 when this process holds no runtime for the Session, and 409 while a Turn is running.
    """
    with translate("Session runtime could not be released", invalid="Invalid Session ID"):
        try:
            evicted = registry.evict(session_id)
        except TimeoutError:
            raise HTTPException(
                status_code=409, detail="A Turn is running on this Session; retry when it completes"
            ) from None
    if not evicted:
        raise HTTPException(status_code=404, detail="Session has no active runtime")
    return Deleted(deleted=session_id)


@router.get("/sessions/{session_id}/config")
def session_config(runtime: Runtime, settings: Settings) -> SessionConfig:
    """Describe a Session's mode, models, context, configuration, and limits."""
    manager = runtime.sandbox_mgr
    return SessionConfig(
        mode=runtime.execution_mode,
        models={"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model},
        context=runtime.context.to_descriptor() if runtime.context else None,
        configuration=runtime_configuration(settings, runtime),
        limits=Limits(
            lm_calls=manager.max_llm_calls,
            depth=manager.max_depth,
            children=manager.max_children,
            active_children=manager.max_active_children,
            timeout_seconds=manager.turn_timeout_seconds,
        ),
    )


@router.put("/sessions/{session_id}/model", responses=LOCKED)
def select_model(
    session_id: str, body: ModelRequest, runtime: Runtime, registry: Registry, settings: Settings
) -> ModelSelection:
    """Bind a model to a role, persist it, and swap in a new engine.

    Returns 409 while a Turn holds the Session lock.
    """
    unavailable = "Model selection is unavailable; configuration unchanged"
    with translate(unavailable, invalid="Invalid session or model configuration"):
        scope = ".".join(body.model.removeprefix("openai/").split(".")[:2])
        if body.model not in settings.list_models(scope):
            raise HTTPException(status_code=422, detail="Model is not an accessible chat service")
        with session_lock(runtime):
            models = {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model, body.role: body.model}
            engine = registry.engine_for_models(runtime, models)
            runtime.harness.store.save_runtime_settings(session_id, {"models": models})
            runtime.set_engine(engine)
    return ModelSelection(role=body.role, model=body.model)


@router.get("/sessions/{session_id}/mode")
def get_mode(runtime: Runtime) -> Mode:
    """Return a Session's current execution mode."""
    return Mode(mode=runtime.execution_mode)


@router.put("/sessions/{session_id}/mode", responses=LOCKED)
def set_mode(session_id: str, body: ModeRequest, runtime: Runtime) -> Mode:
    """Persist a new execution mode and apply it to the Session runtime.

    The current context is saved with the mode.
    """
    with translate("Session runtime is unavailable", invalid="Invalid Session ID or mode"), session_lock(runtime):
        context = runtime.context.to_descriptor() if runtime.context else None
        runtime.harness.store.save_runtime_settings(session_id, {"mode": body.mode, "context": context})
        runtime.set_mode(body.mode)
    return Mode(mode=body.mode)


@router.get("/sessions/{session_id}/history")
def get_history(session_id: str, store: StoreDep) -> History:
    """Return a Session's stored Turns without claiming its runtime lease."""
    with translate("Session history is unavailable", invalid="Invalid Session ID"):
        session_id = validate_session_id(session_id)
        return History(session_id=session_id, turns=store.history(session_id))


@router.get("/sessions/{session_id}/turns/{turn_id}")
def turn_status(session_id: str, turn_id: str, store: StoreDep) -> TurnStatus:
    """Return one durable Turn status, or 404 when the Turn is unknown.

    A Turn stored before provenance existed stays readable; its result is labelled
    `pre-provenance` rather than being rejected or backfilled. A Turn with recorded
    deadline policy but incomplete snapshot fields is `unresolved`; all six snapshot
    fields must be present for `recorded` provenance.
    """
    with translate("Turn status is unavailable", invalid="Invalid session or turn ID"):
        result = store.turn_status(validate_session_id(session_id), validate_session_id(turn_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Turn was not found")
    stored = result.get("result")
    if isinstance(stored, dict):
        from fleet_rlm.sandbox.snapshots import provenance_state

        result = {**result, "result": {**stored, "provenance_state": provenance_state(stored)}}
    return TurnStatus.model_validate(result)


@router.put("/sessions/{session_id}/context", responses=LOCKED)
def select_context(session_id: str, body: ContextRequest, runtime: Runtime) -> ContextSelected:
    """Select a workspace file as the Session's context and persist it.

    The response reports the file's size and SHA-256 digest.
    """
    with translate("Context selection is unavailable", invalid="Invalid context selection"), session_lock(runtime):
        metadata = runtime.sandbox_mgr.stat_workspace_file(body.path)
        context = WorkspaceDataset(
            workspace_path=body.path,
            description=body.description,
            size_bytes=metadata["size_bytes"],
            sha256=metadata["sha256"],
        )
        runtime.harness.store.save_runtime_settings(
            session_id, {"mode": runtime.execution_mode, "context": context.to_descriptor()}
        )
        runtime.context = context
    return ContextSelected(path=body.path, size_bytes=context.size_bytes, sha256=context.sha256)


@router.delete("/sessions/{session_id}/context", responses=LOCKED)
def clear_context(session_id: str, runtime: Runtime) -> Status:
    """Clear the Session's selected context and persist the change."""
    with translate("Context selection is unavailable"), session_lock(runtime):
        runtime.harness.store.save_runtime_settings(session_id, {"mode": runtime.execution_mode, "context": None})
        runtime.context = None
    return Status(status="cleared")


@memory_router.get("/sessions/{session_id}/memory")
def list_memory(
    runtime: Runtime,
    q: Annotated[str, Query(description="Optional search query to filter memories")] = "",
) -> Memories:
    """List a Session's memory entries that match an optional query."""
    with translate("Session memory is unavailable"):
        return Memories(memories=runtime.harness.memories(q))


@memory_router.put("/sessions/{session_id}/memory/{memory_id}", responses=LOCKED)
def put_memory(memory_id: str, body: MemoryRequest, runtime: Runtime) -> MemorySaved:
    """Save one memory entry under the given ID."""
    with translate("Session memory is unavailable", invalid="Invalid memory"), session_lock(runtime):
        runtime.harness.save_memory(
            memory_id, body.content, sources=body.sources, kind=body.kind, reviewed=body.reviewed
        )
    return MemorySaved(memory_id=memory_id)


@memory_router.delete("/sessions/{session_id}/memory/{memory_id}", responses=LOCKED)
def remove_memory(memory_id: str, runtime: Runtime) -> Deleted:
    """Delete one memory entry by ID."""
    with translate("Session memory is unavailable", invalid="Invalid memory ID"), session_lock(runtime):
        runtime.harness.delete_memory(memory_id)
    return Deleted(deleted=memory_id)


def skills_runtime(registry: Registry, session_id: str = "default") -> SessionRuntime:
    """Resolve the Session runtime that a Skills request operates on."""
    with translate("Session runtime is unavailable", invalid="Invalid Session ID"):
        return registry.get(session_id)


SkillsRuntime = Annotated[SessionRuntime, Depends(skills_runtime)]


@skills_router.get("/skills", responses=LOCKED)
def get_skills_catalog(runtime: SkillsRuntime) -> Catalog:
    """Sync local Skills to the Session's volume and return the catalog XML."""
    with translate("Session Skill catalog is unavailable"), session_lock(runtime):
        runtime.skill_mgr.sync_local_skills_to_volume()
        return Catalog(catalog_xml=runtime.skill_mgr.build_catalog_xml())


@skills_router.post("/skills/pull", responses=LOCKED)
def pull_skills_to_local(runtime: SkillsRuntime) -> Exported:
    """Export the Session's volume Skills to local storage."""
    with translate("Skill export is unavailable"), session_lock(runtime):
        return Exported(exported=runtime.skill_mgr.export_volume_skills_to_local())


@skills_router.post("/skills/push", responses=LOCKED)
def push_skills(runtime: SkillsRuntime) -> Pushed:
    """Push local Skills to the Session's volume and return the count."""
    with translate("Skill push is unavailable"), session_lock(runtime):
        return Pushed(pushed=int(runtime.skill_mgr.sync_local_skills_to_volume()))
