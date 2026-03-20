"""Shared chat/session normalization helpers for the Daytona workbench agent."""

from __future__ import annotations

import json
from typing import Any

from .types import ContextSource, DaytonaRunResult


def render_final_text(value: Any) -> str:
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


def render_cancelled_text(result: DaytonaRunResult) -> str:
    warnings = list(result.summary.warnings or [])
    base = result.summary.error or "Daytona run cancelled."
    if warnings:
        return f"{base}\n\nWarnings:\n- " + "\n- ".join(warnings)
    return str(base)


def dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in paths:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def history_messages(history: Any) -> list[dict[str, str]]:
    messages = getattr(history, "messages", [])
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    return []


def normalize_history_turn(raw: dict[str, Any]) -> dict[str, str] | None:
    user_request = str(raw.get("user_request", "") or "").strip()
    assistant_response = render_final_text(raw.get("assistant_response", "")).strip()
    if not user_request and not assistant_response:
        return None
    return {
        "user_request": user_request,
        "assistant_response": assistant_response,
    }


def normalized_history_messages(history: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in history_messages(history):
        turn = normalize_history_turn(item)
        if turn is not None:
            normalized.append(turn)
    return normalized


def normalized_context_sources(raw: Any) -> list[ContextSource]:
    if not isinstance(raw, list):
        return []
    normalized: list[ContextSource] = []
    for item in raw:
        try:
            normalized.append(ContextSource.from_raw(item))
        except Exception:
            continue
    return normalized
