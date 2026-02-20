"""Stub router for Memory."""

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("")
async def list_memory() -> dict[str, Any]:
    return {"items": []}


@router.post("")
async def create_memory_item() -> dict[str, Any]:
    return {"status": "ok"}
