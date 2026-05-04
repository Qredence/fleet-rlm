"""Shared type aliases for OpenAPI router response definitions."""

from __future__ import annotations

from typing import Any, TypeAlias

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]

__all__ = ["OpenAPIResponses"]
