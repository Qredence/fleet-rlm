"""Stub router for Sandbox (Filesystem)."""

from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.get("")
async def get_sandbox() -> dict[str, Any]:
    return {"files": []}


@router.get("/file")
async def get_sandbox_file() -> dict[str, Any]:
    return {"content": ""}
