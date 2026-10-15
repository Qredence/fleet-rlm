"""A Session's reviewed and unreviewed memories."""

from __future__ import annotations

from fastapi import APIRouter

from fleet_rlm.api.deps import Runtime
from fleet_rlm.api.errors import translate
from fleet_rlm.api.schemas import Deleted, Memories, MemoryRequest, MemorySaved

router = APIRouter(tags=["memory"])


@router.get("/sessions/{session_id}/memory", response_model=Memories)
def list_memory(runtime: Runtime, q: str = "") -> dict:
    with translate("Session memory is unavailable"):
        return {"memories": runtime.harness.memories(q)}


@router.put("/sessions/{session_id}/memory/{memory_id}", response_model=MemorySaved)
def put_memory(memory_id: str, body: MemoryRequest, runtime: Runtime) -> dict:
    with translate("Session memory is unavailable", invalid="Invalid memory"), runtime.turn_lock:
        runtime.harness.save_memory(
            memory_id, body.content, sources=body.sources, kind=body.kind, reviewed=body.reviewed
        )
    return {"memory_id": memory_id}


@router.delete("/sessions/{session_id}/memory/{memory_id}", response_model=Deleted)
def remove_memory(memory_id: str, runtime: Runtime) -> dict:
    with translate("Session memory is unavailable", invalid="Invalid memory ID"), runtime.turn_lock:
        runtime.harness.delete_memory(memory_id)
    return {"deleted": memory_id}
