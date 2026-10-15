"""User Skills: the Session catalog, and sync between `.fleet/skills/` and the Session Volume."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from fleet_rlm.api.deps import Registry
from fleet_rlm.api.errors import translate
from fleet_rlm.api.schemas import Catalog, Exported, Pushed
from fleet_rlm.session import SessionRuntime

router = APIRouter(tags=["skills"])


def skills_runtime(registry: Registry, session_id: str = "default") -> SessionRuntime:
    with translate("Session runtime is unavailable", invalid="Invalid Session ID"):
        return registry.get(session_id)


SkillsRuntime = Annotated[SessionRuntime, Depends(skills_runtime)]


@router.get("/skills", response_model=Catalog)
def get_skills_catalog(runtime: SkillsRuntime) -> dict:
    with translate("Session Skill catalog is unavailable"), runtime.turn_lock:
        runtime.skill_mgr.sync_local_skills_to_volume()
        return {"catalog_xml": runtime.skill_mgr.build_catalog_xml()}


@router.post("/skills/pull", response_model=Exported)
def pull_skills_to_local(runtime: SkillsRuntime) -> dict:
    with translate("Skill export is unavailable"), runtime.turn_lock:
        return {"exported": runtime.skill_mgr.export_volume_skills_to_local()}


@router.post("/skills/push", response_model=Pushed)
def push_skills(runtime: SkillsRuntime) -> dict:
    with translate("Skill push is unavailable"), runtime.turn_lock:
        return {"pushed": int(runtime.skill_mgr.sync_local_skills_to_volume())}
