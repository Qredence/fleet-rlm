"""Consolidated placeholder routers for planned API surfaces.

These route groups are intentionally minimal stubs so contributors can see the
reserved API prefixes without implying completed functionality via many
near-empty modules.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])
taxonomy_router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])
search_router = APIRouter(prefix="/search", tags=["search"])
memory_router = APIRouter(prefix="/memory", tags=["memory"])
sandbox_router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@analytics_router.get("")
async def get_analytics() -> dict[str, Any]:
    return {}


@analytics_router.get("/skills/{skill_id}")
async def get_skill_analytics(skill_id: str) -> dict[str, Any]:
    _ = skill_id
    return {}


@taxonomy_router.get("")
async def get_taxonomy() -> list[Any]:
    return []


@taxonomy_router.get("/{path:path}")
async def get_taxonomy_by_path(path: str) -> dict[str, Any]:
    _ = path
    return {}


@search_router.get("")
async def search() -> dict[str, Any]:
    return {"results": []}


@memory_router.get("")
async def list_memory() -> dict[str, Any]:
    return {"items": []}


@memory_router.post("")
async def create_memory_item() -> dict[str, Any]:
    return {"status": "ok"}


@sandbox_router.get("")
async def get_sandbox() -> dict[str, Any]:
    return {"files": []}


@sandbox_router.get("/file")
async def get_sandbox_file() -> dict[str, Any]:
    return {"content": ""}
