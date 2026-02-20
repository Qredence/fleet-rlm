"""Stub router for Search."""

from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search() -> dict[str, Any]:
    return {"results": []}
