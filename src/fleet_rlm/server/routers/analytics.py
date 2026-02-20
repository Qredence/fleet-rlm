"""Stub router for Analytics."""

from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
async def get_analytics() -> dict[str, Any]:
    return {}


@router.get("/skills/{skill_id}")
async def get_skill_analytics(skill_id: str) -> dict[str, Any]:
    return {}
