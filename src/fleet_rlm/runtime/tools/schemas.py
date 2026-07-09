"""Pydantic schemas for runtime tool inputs and outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fleet_rlm.skills.schemas import (
    ListSkillsOutput,
    LoadSkillInput,
    LoadSkillOutput,
    ReadSkillResourceOutput,
)


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query string")
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum number of results to return")


class WebSearchResult(BaseModel):
    url: str
    title: str
    snippet: str


class WebSearchOutput(BaseModel):
    status: str
    provider: str
    results: list[WebSearchResult]
    count: int
    error: str | None = None


class FetchPageInput(BaseModel):
    url: str = Field(..., description="HTTP(S) URL to fetch")


class FetchPageOutput(BaseModel):
    status: str
    url: str
    text: str = ""
    char_count: int = 0
    error: str | None = None


class SearchKnowledgeInput(BaseModel):
    query: str = Field(..., description="Knowledge search query")
    max_results: int = Field(default=20, ge=1, le=100, description="Maximum results to return")


class KnowledgeResult(BaseModel):
    doc_id: str
    source: str
    path: str
    alias: str
    tags: list[str] = []


class SearchKnowledgeOutput(BaseModel):
    status: str
    query: str
    results: list[KnowledgeResult]
    count: int
    error: str | None = None


class KnowledgePersistResult(BaseModel):
    doc_id: str
    knowledge_path: str
    index_path: str


class LoadDocumentInput(BaseModel):
    source: str = Field(..., description="Local file path or HTTP(S) URL")
    alias: str = Field(default="active", description="Document alias")


class LoadDocumentDirectoryOutput(BaseModel):
    status: str
    alias: str
    path: str
    files: list[str]
    total_count: int
    hint: str


class LoadDocumentOutput(BaseModel):
    status: str
    alias: str
    path: str
    char_count: int
    line_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    knowledge: KnowledgePersistResult | None = None
    error: str | None = None


__all__ = [
    "WebSearchInput",
    "WebSearchResult",
    "WebSearchOutput",
    "FetchPageInput",
    "FetchPageOutput",
    "SearchKnowledgeInput",
    "KnowledgeResult",
    "SearchKnowledgeOutput",
    "LoadSkillInput",
    "LoadSkillOutput",
    "ListSkillsOutput",
    "ReadSkillResourceOutput",
    "KnowledgePersistResult",
    "LoadDocumentInput",
    "LoadDocumentOutput",
    "LoadDocumentDirectoryOutput",
]
