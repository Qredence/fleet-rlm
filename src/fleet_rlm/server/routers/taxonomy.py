"""Stub router for Taxonomy."""

from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("")
async def get_taxonomy() -> list[Any]:
    return []


@router.get("/{path:path}")
async def get_taxonomy_by_path(path: str) -> dict[str, Any]:
    return {}
