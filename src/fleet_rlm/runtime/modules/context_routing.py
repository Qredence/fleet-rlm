"""Large-context detection and auto-routing to dspy.RLM."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.modules.variable_mode import VARIABLE_MODE_THRESHOLD


def large_context_threshold_chars() -> int:
    raw = os.environ.get("FLEET_RLM_LARGE_CONTEXT_THRESHOLD", "")
    try:
        if raw.strip():
            return max(1, int(raw))
    except ValueError:
        pass
    return VARIABLE_MODE_THRESHOLD


def _path_char_estimate(path: str) -> int:
    stripped = path.strip()
    if not stripped:
        return 0
    candidate = Path(stripped)
    if not candidate.exists():
        return len(stripped)
    if candidate.is_file():
        try:
            return candidate.stat().st_size
        except OSError:
            return len(stripped)
    if candidate.is_dir():
        total = 0
        try:
            for item in candidate.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
        except OSError:
            return len(stripped)
        return total
    return len(stripped)


def _history_char_estimate(history: dspy.History | None) -> int:
    messages = list(getattr(history, "messages", []) or []) if history is not None else []
    total = 0
    for message in messages:
        if isinstance(message, dict):
            for key in ("user_message", "user_request", "response", "assistant_response", "answer"):
                value = message.get(key)
                if value not in (None, ""):
                    total += len(str(value))
        else:
            for key in ("user_message", "user_request", "response", "assistant_response", "answer"):
                value = getattr(message, key, None)
                if value not in (None, ""):
                    total += len(str(value))
    return total


def estimate_turn_context_chars(
    *,
    user_request: str,
    history: dspy.History | None = None,
    docs_path: str | None = None,
    context_paths: list[str] | None = None,
    loaded_document_paths: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Return total estimated context size and contributing source labels."""
    sources: list[str] = []
    total = len(user_request or "")

    history_chars = _history_char_estimate(history)
    if history_chars:
        total += history_chars
        sources.append(f"history:{history_chars}")

    if docs_path:
        docs_chars = _path_char_estimate(docs_path)
        total += docs_chars
        sources.append(f"docs_path:{docs_path}:{docs_chars}")

    for path in context_paths or []:
        path_chars = _path_char_estimate(path)
        if path_chars:
            total += path_chars
            sources.append(f"context_path:{path}:{path_chars}")

    for path in loaded_document_paths or []:
        path_chars = _path_char_estimate(path)
        if path_chars:
            total += path_chars
            sources.append(f"loaded_document:{path}:{path_chars}")

    return total, sources


def build_turn_context(
    *,
    user_request: str,
    history: dspy.History | None = None,
    docs_path: str | None = None,
    context_paths: list[str] | None = None,
    repo_url: str | None = None,
    repo_ref: str | None = None,
    loaded_document_paths: list[str] | None = None,
) -> TurnContext:
    threshold = large_context_threshold_chars()
    estimated, sources = estimate_turn_context_chars(
        user_request=user_request,
        history=history,
        docs_path=docs_path,
        context_paths=context_paths,
        loaded_document_paths=loaded_document_paths,
    )
    return TurnContext(
        docs_path=(docs_path or "").strip() or None,
        context_paths=[str(item).strip() for item in (context_paths or []) if str(item).strip()],
        repo_url=(repo_url or "").strip() or None,
        repo_ref=(repo_ref or "").strip() or None,
        estimated_chars=estimated,
        threshold_chars=threshold,
        context_sources=sources,
    )


def should_auto_route_large_context(*, execution_mode: str, turn_context: TurnContext | None) -> bool:
    if execution_mode != "auto" or turn_context is None:
        return False
    return turn_context.estimated_chars >= turn_context.threshold_chars


def load_large_context_rlm_kwargs(
    turn_context: TurnContext | None,
    *,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Build optional dspy.RLM kwargs for large local context."""
    if turn_context is None:
        return {}

    kwargs: dict[str, Any] = {}
    manifest: dict[str, str] = {}

    docs_path = turn_context.docs_path
    if docs_path:
        path = Path(docs_path)
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            kwargs["document_text"] = text
            manifest["docs_path"] = docs_path
            kwargs["source_metadata"] = {
                "status": "ok" if text else "empty",
                "char_count": str(len(text)),
                "source": "local_file",
                "path": docs_path,
            }

    staged_paths = list(turn_context.context_paths)
    if staged_paths:
        kwargs["context_paths"] = staged_paths
        for item in staged_paths:
            manifest[item] = str(_path_char_estimate(item))
        kwargs["context_manifest"] = manifest

    if interpreter is not None and staged_paths and "document_text" not in kwargs:
        volume_mount = getattr(interpreter, "volume_mount_path", None)
        if volume_mount:
            kwargs.setdefault("source_metadata", {})
            kwargs["source_metadata"]["volume_mount_path"] = str(volume_mount)

    return kwargs


__all__ = [
    "TurnContext",
    "build_turn_context",
    "estimate_turn_context_chars",
    "large_context_threshold_chars",
    "load_large_context_rlm_kwargs",
    "should_auto_route_large_context",
]
