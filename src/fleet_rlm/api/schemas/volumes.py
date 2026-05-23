"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VolumeProvider = Literal["daytona"]


class VolumeTreeNode(BaseModel):
    """A single node in the volume file tree."""

    id: str = Field(description="Stable node identifier used by the frontend tree view.")
    name: str = Field(description="Display name for the file-system node.")
    path: str = Field(description="Absolute path for the file-system node within the runtime volume.")
    type: Literal["volume", "directory", "file"] = Field(
        description="Kind of file-system node represented by this entry."
    )
    children: list[VolumeTreeNode] = Field(
        default_factory=list,
        description="Child nodes for directory or volume entries.",
    )
    size: int | None = Field(
        default=None,
        description="File size in bytes when the provider reports one.",
    )
    modified_at: str | None = Field(
        default=None,
        description="Last modified timestamp when the provider reports one.",
    )


class VolumeTreeResponse(BaseModel):
    """Response for the volume tree listing endpoint."""

    provider: VolumeProvider = Field(description="Runtime volume backend used to satisfy the request.")
    volume_name: str = Field(description="Resolved volume name used for the listing request.")
    root_path: str = Field(description="Normalized root path used for the listing request.")
    allowed_roots: list[str] = Field(
        default_factory=list,
        description="Canonical volume roots that may be addressed by tree and file requests.",
    )
    nodes: list[VolumeTreeNode] = Field(description="Tree nodes rooted at the requested path.")
    total_files: int = Field(
        default=0,
        description="Total file count returned in the current response payload.",
    )
    total_dirs: int = Field(
        default=0,
        description="Total directory count returned in the current response payload.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the provider truncated the tree because of depth or payload limits.",
    )
    max_depth: int = Field(description="Depth limit applied to the tree request.")
    max_entries: int = Field(description="Entry limit applied to the tree request.")
    entries_returned: int = Field(description="Total node entries returned in this response.")


class VolumeFileContentResponse(BaseModel):
    """Response for runtime volume file-content preview endpoint."""

    provider: VolumeProvider = Field(description="Runtime volume backend used to satisfy the request.")
    path: str = Field(description="Normalized file path used for the preview request.")
    mime: str = Field(description="Detected MIME type for the returned content.")
    size: int = Field(description="File size in bytes reported by the provider.")
    sha256: str | None = Field(
        default=None,
        description="SHA-256 hex digest of the full file bytes before truncation.",
    )
    encoding: str | None = Field(
        default=None,
        description=(
            "Content encoding: 'utf-8' for clean text, 'utf-8-lossy' when replacement "
            "characters were introduced, or 'binary' for non-text files."
        ),
    )
    content: str = Field(description="UTF-8 text preview returned for the requested file. Empty for binary files.")
    binary: bool = Field(
        default=False,
        description="True when the file was detected as binary; content will be empty.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the returned file content was truncated to respect max_bytes.",
    )


class VolumeListItem(BaseModel):
    """Single volume entry returned by the volume list endpoint."""

    id: str = Field(description="Volume identifier.")
    name: str = Field(description="Volume name.")
    state: str = Field(default="", description="Volume state (e.g. ready, creating).")
    created_at: str | None = Field(default=None, description="ISO-8601 creation timestamp when available.")


class VolumeListResponse(BaseModel):
    """Response for the volume list endpoint."""

    provider: VolumeProvider = Field(description="Runtime volume backend used to satisfy the request.")
    volumes: list[VolumeListItem] = Field(default_factory=list, description="Available persistent volumes.")
