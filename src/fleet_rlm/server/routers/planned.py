"""Consolidated placeholder routers for planned API surfaces.

These route groups are intentionally minimal stubs so contributors can see the
reserved API prefixes without implying completed functionality via many
near-empty modules.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])
taxonomy_router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])
search_router = APIRouter(prefix="/search", tags=["search"])
memory_router = APIRouter(prefix="/memory", tags=["memory"])
sandbox_router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def _planned_not_implemented(endpoint: str) -> NoReturn:
    """Raise a consistent 501 response for planned-only endpoints."""
    raise HTTPException(
        status_code=501,
        detail=f"{endpoint} endpoint not yet implemented",
    )


@analytics_router.get("")
async def get_analytics() -> dict[str, Any]:
    _planned_not_implemented("analytics")


@analytics_router.get("/skills/{skill_id}")
async def get_skill_analytics(skill_id: str) -> dict[str, Any]:
    _ = skill_id
    _planned_not_implemented("analytics skill")


@taxonomy_router.get("")
async def get_taxonomy() -> list[Any]:
    _planned_not_implemented("taxonomy")


@taxonomy_router.get("/{path:path}")
async def get_taxonomy_by_path(path: str) -> dict[str, Any]:
    _ = path
    _planned_not_implemented("taxonomy path")


@search_router.get("")
async def search() -> dict[str, Any]:
    _planned_not_implemented("search")


@memory_router.get("")
async def list_memory() -> dict[str, Any]:
    _planned_not_implemented("memory list")


@memory_router.post("")
async def create_memory_item() -> dict[str, Any]:
    _planned_not_implemented("memory create")


@sandbox_router.get("")
async def get_sandbox() -> dict[str, Any]:
    _planned_not_implemented("sandbox")


@sandbox_router.get("/file")
async def get_sandbox_file() -> dict[str, Any]:
    _planned_not_implemented("sandbox file")
