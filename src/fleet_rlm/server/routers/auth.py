"""Stub router for Authentication."""

from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login() -> dict[str, Any]:
    return {"token": "dummy_token"}


@router.post("/logout")
async def logout() -> dict[str, Any]:
    return {"status": "ok"}


@router.get("/me")
async def get_me() -> dict[str, Any]:
    return {"id": "user-1", "name": "Admin User"}
