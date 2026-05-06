"""Serializable payload models and state normalization for Daytona flows."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_optional_text(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    if limit is not None and len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


class SandboxLmRuntimeConfig(BaseModel):
    """Serializable LM bootstrap config passed into sandbox-local runtimes."""

    model: str
    api_key: str
    api_base: str | None = None
    max_tokens: int = 64_000
    delegate_model: str | None = None
    delegate_api_key: str | None = None
    delegate_api_base: str | None = None

    @field_validator("model", "api_key", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_optional_text(value)
        if text is None:
            raise ValueError("Sandbox LM config requires model and api_key.")
        return text

    @field_validator(
        "api_base",
        "delegate_model",
        "delegate_api_key",
        "delegate_api_base",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text_fields(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("max_tokens", mode="before")
    @classmethod
    def _normalize_max_tokens(cls, value: Any) -> int:
        if value is None or value == "":
            return 64_000
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 64_000
        return parsed if parsed > 0 else 64_000

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_raw(cls, raw: Any) -> SandboxLmRuntimeConfig:
        if not isinstance(raw, dict):
            raise ValueError("Sandbox LM config must be a dict.")
        try:
            return cls.model_validate(raw)
        except Exception as exc:  # pragma: no cover - pydantic internals
            raise ValueError("Sandbox LM config requires model and api_key.") from exc


class ContextSource(BaseModel):
    """Host-sourced local context staged into the Daytona workspace."""

    source_id: str
    kind: str
    host_path: str
    staged_path: str
    source_type: str | None = None
    extraction_method: str | None = None
    file_count: int = 1
    skipped_count: int = 0
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_id", "kind", "host_path", "staged_path", mode="before")
    @classmethod
    def _normalize_required_fields(cls, value: Any) -> str:
        text = _normalize_optional_text(value)
        if text is None:
            raise ValueError("Context source requires source_id, kind, host_path, and staged_path.")
        return text

    @field_validator("source_type", "extraction_method", mode="before")
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("file_count", mode="before")
    @classmethod
    def _normalize_file_count(cls, value: Any) -> int:
        if value is None or value == "":
            return 1
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 1
        return parsed if parsed > 0 else 1

    @field_validator("skipped_count", mode="before")
    @classmethod
    def _normalize_skipped_count(cls, value: Any) -> int:
        if value is None or value == "":
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed >= 0 else 0

    @field_validator("warnings", mode="before")
    @classmethod
    def _normalize_warnings(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, (str, bytes)):
            items = [value]
        else:
            try:
                items = list(value)
            except TypeError:
                items = [value]
        normalized: list[str] = []
        for item in items:
            text = _normalize_optional_text(item)
            if text is not None:
                normalized.append(text)
        return normalized

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_raw(cls, raw: Any) -> ContextSource:
        if not isinstance(raw, dict):
            raise ValueError("Context source payload must be a dict.")
        try:
            return cls.model_validate(raw)
        except Exception as exc:  # pragma: no cover - pydantic internals
            raise ValueError("Context source requires source_id, kind, host_path, and staged_path.") from exc


def render_final_text(value: Any) -> str:
    """Extract and normalize final text from structured output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("final_markdown", "summary", "text", "content", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        nested_value = value.get("value")
        if nested_value is not value:
            nested_text = render_final_text(nested_value)
            if nested_text:
                return nested_text
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def history_messages(history: Any) -> list[dict[str, str]]:
    """Extract message list from a history object."""
    messages = getattr(history, "messages", [])
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    return []


def normalize_history_turn(raw: dict[str, Any]) -> dict[str, str] | None:
    """Normalize a single history turn into ``{user_request, assistant_response}``."""
    user_request = str(raw.get("user_request", "") or "").strip()
    assistant_response = render_final_text(raw.get("assistant_response", "")).strip()
    if not user_request and not assistant_response:
        return None
    return {
        "user_request": user_request,
        "assistant_response": assistant_response,
    }


def normalized_history_messages(history: Any) -> list[dict[str, str]]:
    """Return a clean list of normalized history turns."""
    normalized: list[dict[str, str]] = []
    for item in history_messages(history):
        turn = normalize_history_turn(item)
        if turn is not None:
            normalized.append(turn)
    return normalized


def normalized_context_sources(raw: Any) -> list[ContextSource]:
    """Normalize a raw sources list into validated ``ContextSource`` objects."""
    if not isinstance(raw, list):
        return []
    normalized: list[ContextSource] = []
    for item in raw:
        try:
            normalized.append(ContextSource.from_raw(item))
        except Exception:
            continue
    return normalized


__all__ = [
    "ContextSource",
    "SandboxLmRuntimeConfig",
    "history_messages",
    "normalize_history_turn",
    "normalized_context_sources",
    "normalized_history_messages",
    "render_final_text",
]
