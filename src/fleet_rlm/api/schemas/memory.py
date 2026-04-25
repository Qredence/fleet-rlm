"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations


from pydantic import BaseModel, Field


class MemoryItemResponse(BaseModel):
    """Single memory item returned by the memory browse endpoint."""

    id: str = Field(description="Durable memory item identifier.")
    scope: str = Field(
        description="Memory scope (e.g. user, tenant, workspace, run, session)."
    )
    scope_id: str = Field(description="Identifier within the scope.")
    kind: str = Field(description="Memory kind (e.g. fact, observation, preference).")
    source: str = Field(description="Memory source (e.g. user, agent, system).")
    status: str = Field(description="Memory status (e.g. active, archived).")
    content_text: str | None = Field(
        default=None, description="Textual content when available."
    )
    importance: int = Field(description="Importance score (0-100).")
    tags: list[str] = Field(default_factory=list, description="Associated tags.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")


class MemoryListResponse(BaseModel):
    """Paginated memory item list response."""

    items: list[MemoryItemResponse] = Field(description="Memory item list items.")
    total: int = Field(description="Total matching memory items.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
