"""Large-context detection and auto-routing to dspy.RLM."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.content.ingestion import read_document_content
from fleet_rlm.runtime.modules.factory import VARIABLE_MODE_THRESHOLD

logger = logging.getLogger(__name__)

_EXTRACTABLE_CONTEXT_SUFFIXES = frozenset(
    {
        ".pdf",
        ".html",
        ".htm",
        ".md",
        ".markdown",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".rtf",
        ".doc",
        ".docx",
    }
)


@dataclass(frozen=True, slots=True)
class InlineContextPayload:
    """Oversized inline payload staged out of the action prompt."""

    text: str
    shortened_user_request: str
    metadata: dict[str, str]


def large_context_threshold_chars() -> int:
    raw = os.environ.get("FLEET_RLM_LARGE_CONTEXT_THRESHOLD", "")
    try:
        if raw.strip():
            return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid FLEET_RLM_LARGE_CONTEXT_THRESHOLD value %r; using default threshold %d",
            raw,
            VARIABLE_MODE_THRESHOLD,
        )
    return VARIABLE_MODE_THRESHOLD


def _metadata_for_inline_payload(
    *,
    extraction_kind: str,
    original_user_request: str,
    staged_text: str,
    shortened_user_request: str,
    delimiter: str | None = None,
) -> dict[str, str]:
    metadata = {
        "inline_context_staged": "true",
        "inline_context_extraction_kind": extraction_kind,
        "original_user_request_chars": str(len(original_user_request or "")),
        "staged_document_chars": str(len(staged_text or "")),
        "short_user_request_chars": str(len(shortened_user_request or "")),
    }
    if delimiter:
        metadata["inline_context_delimiter"] = delimiter
    return metadata


def extract_inline_context_payload(
    user_request: str,
    *,
    threshold_chars: int,
) -> InlineContextPayload | None:
    """Extract an oversized pasted payload from ``user_request`` for RLM staging.

    The RLM action prompt should carry the task and a pointer to staged data, not
    repeat an 80k+ character paste on every iteration. A ``CONTEXT:`` section is
    treated as the safest split point. If no delimiter exists but the request is
    still over threshold, stage the whole request and ask the RLM to inspect it
    from ``context["document_text"]``.
    """
    request = str(user_request or "")
    if len(request) < threshold_chars:
        return None

    match = re.search(r"(?im)^([ \t]*(?:#{1,6}[ \t]*)?context[ \t]*:)", request)
    if match is not None:
        staged_text = request[match.start() :].strip()
        instruction = request[: match.start()].strip()
        if not instruction:
            instruction = "Use the staged inline context to answer the user's request."
        shortened = (
            f"{instruction}\n\n"
            "[Staged inline context]\n"
            'The full CONTEXT block is available as context["document_text"] in the RLM REPL. '
            "Inspect it with Python, use focused llm_query calls only when needed, and submit the final answer."
        )
        return InlineContextPayload(
            text=staged_text,
            shortened_user_request=shortened,
            metadata=_metadata_for_inline_payload(
                extraction_kind="context_delimiter",
                original_user_request=request,
                staged_text=staged_text,
                shortened_user_request=shortened,
                delimiter=match.group(1).strip(),
            ),
        )

    shortened = (
        "[Staged inline context]\n"
        "The full user request was too large for repeated action prompts and has been staged as "
        'context["document_text"] in the RLM REPL. Inspect that variable with Python to recover the '
        "task, data, constraints, and output format before answering."
    )
    return InlineContextPayload(
        text=request,
        shortened_user_request=shortened,
        metadata=_metadata_for_inline_payload(
            extraction_kind="full_request",
            original_user_request=request,
            staged_text=request,
            shortened_user_request=shortened,
        ),
    )


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
        for key in ("user_message", "response"):
            value = message.get(key) if isinstance(message, dict) else getattr(message, key, None)
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


def resolve_effective_context_paths(
    *,
    message_context_paths: list[str] | None = None,
    loaded_document_paths: list[str] | None = None,
    session_context_paths: list[str] | None = None,
) -> list[str]:
    """Merge explicit message paths with persisted session/loaded context.

    Follow-up turns often omit the PDF path in chat text. When the current message
    carries no paths, reuse staged interpreter paths or paths loaded earlier in
    the session so large-context routing and ``document_text`` injection continue
    to work.
    """
    explicit = [str(item).strip() for item in (message_context_paths or []) if str(item).strip()]
    if explicit:
        return explicit

    merged: list[str] = []
    for source in (loaded_document_paths or [], session_context_paths or []):
        for item in source:
            stripped = str(item).strip()
            if stripped and stripped not in merged:
                merged.append(stripped)
    return merged


def interpreter_session_context_paths(interpreter: Any | None) -> list[str] | None:
    """Return persisted interpreter context paths for session-scoped routing."""
    if interpreter is None:
        return None
    paths = getattr(interpreter, "context_paths", []) or []
    return [str(item) for item in paths] if paths else None


def build_turn_context_for_agent(
    agent: Any,
    *,
    user_request: str,
    docs_path: str | None = None,
    context_paths: list[str] | None = None,
    history: dspy.History | None = None,
) -> TurnContext:
    """Build turn context from a chat agent and incoming message fields."""
    interpreter = getattr(agent, "interpreter", None)
    loaded_document_paths = getattr(agent, "loaded_document_paths", None)
    return build_turn_context(
        user_request=user_request,
        history=history,
        docs_path=docs_path,
        context_paths=context_paths,
        loaded_document_paths=(
            [str(item) for item in loaded_document_paths] if isinstance(loaded_document_paths, list) else None
        ),
        session_context_paths=interpreter_session_context_paths(interpreter),
    )


def build_turn_context(
    *,
    user_request: str,
    history: dspy.History | None = None,
    docs_path: str | None = None,
    context_paths: list[str] | None = None,
    repo_url: str | None = None,
    repo_ref: str | None = None,
    loaded_document_paths: list[str] | None = None,
    session_context_paths: list[str] | None = None,
) -> TurnContext:
    effective_paths = resolve_effective_context_paths(
        message_context_paths=context_paths,
        loaded_document_paths=loaded_document_paths,
        session_context_paths=session_context_paths,
    )
    threshold = large_context_threshold_chars()
    effective_path_set = set(effective_paths or [])
    extra_loaded_paths = [path for path in (loaded_document_paths or []) if path not in effective_path_set]
    estimated, sources = estimate_turn_context_chars(
        user_request=user_request,
        history=history,
        docs_path=docs_path,
        context_paths=effective_paths,
        loaded_document_paths=extra_loaded_paths or None,
    )
    inline_payload = extract_inline_context_payload(user_request, threshold_chars=threshold)
    if inline_payload is not None:
        sources.append(f"inline_context:{len(inline_payload.text)}")
    return TurnContext(
        docs_path=(docs_path or "").strip() or None,
        context_paths=effective_paths,
        repo_url=(repo_url or "").strip() or None,
        repo_ref=(repo_ref or "").strip() or None,
        estimated_chars=estimated,
        threshold_chars=threshold,
        context_sources=sources,
        inline_context_text=inline_payload.text if inline_payload is not None else "",
        shortened_user_request=inline_payload.shortened_user_request if inline_payload is not None else None,
        inline_context_metadata=inline_payload.metadata if inline_payload is not None else {},
    )


def should_auto_route_large_context(*, execution_mode: str, turn_context: TurnContext | None) -> bool:
    if execution_mode != "auto" or turn_context is None:
        return False
    return turn_context.estimated_chars >= turn_context.threshold_chars


def _load_host_document_text(path: Path) -> tuple[str, dict[str, str]]:
    """Extract readable text from a host-side context file when possible."""
    suffix = path.suffix.lower()
    if suffix in _EXTRACTABLE_CONTEXT_SUFFIXES:
        try:
            text, metadata = read_document_content(path)
            meta = {
                "status": "ok" if text.strip() else "empty",
                "char_count": str(len(text)),
                "source": "local_file",
                "path": str(path),
                "extraction_method": str(metadata.get("extraction_method") or ""),
                "source_type": str(metadata.get("source_type") or suffix.lstrip(".")),
            }
            return text, meta
        except (OSError, ValueError):
            return "", {"status": "error", "path": str(path)}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", {"status": "error", "path": str(path)}
    return text, {
        "status": "ok" if text else "empty",
        "char_count": str(len(text)),
        "source": "local_file",
        "path": str(path),
    }


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
    source_metadata: dict[str, Any] = {}

    docs_path = turn_context.docs_path
    if docs_path:
        path = Path(docs_path)
        if path.is_file():
            text, meta = _load_host_document_text(path)
            if text.strip():
                kwargs["document_text"] = text
            manifest["docs_path"] = docs_path
            source_metadata.update(meta)

    staged_paths = list(turn_context.context_paths)
    if staged_paths:
        kwargs["context_paths"] = staged_paths
        for item in staged_paths:
            manifest[item] = str(_path_char_estimate(item))
        kwargs["context_manifest"] = manifest

        if "document_text" not in kwargs:
            extractable = [
                Path(item)
                for item in staged_paths
                if Path(item).is_file() and Path(item).suffix.lower() in _EXTRACTABLE_CONTEXT_SUFFIXES
            ]
            if len(extractable) == 1:
                text, meta = _load_host_document_text(extractable[0])
                if text.strip():
                    kwargs["document_text"] = text
                    source_metadata.update(meta)

    if staged_paths and "document_text" not in kwargs:
        sandbox_paths: list[str] = []
        if interpreter is not None:
            volume_mount = getattr(interpreter, "volume_mount_path", None)
            if volume_mount:
                source_metadata["volume_mount_path"] = str(volume_mount)

            context_sources = getattr(interpreter, "context_sources", None) or getattr(
                interpreter,
                "_persisted_context_sources",
                None,
            )
            if context_sources:
                for source in context_sources:
                    staged_path = getattr(source, "staged_path", None)
                    if staged_path is None and isinstance(source, dict):
                        staged_path = source.get("staged_path")
                    if staged_path:
                        sandbox_paths.append(str(staged_path))

        if sandbox_paths:
            source_metadata["sandbox_staged_paths"] = sandbox_paths
            source_metadata["context_staging_hint"] = (
                "Use sandbox_staged_paths only. Do not open host filesystem paths from context_paths."
            )
        else:
            source_metadata["context_staging_hint"] = (
                "Host paths in context_paths are not readable inside the Daytona REPL. "
                "Use sandbox workspace paths from sandbox_staged_paths or read "
                ".fleet-rlm/context/manifest.json and open each staged_path .extracted.txt file."
            )

    if turn_context.inline_context_text:
        inline_text = str(turn_context.inline_context_text)
        existing_text = str(kwargs.get("document_text") or "")
        if existing_text.strip():
            kwargs["document_text"] = f"{existing_text}\n\n--- STAGED INLINE CONTEXT ---\n\n{inline_text}"
        else:
            kwargs["document_text"] = inline_text
        source_metadata.update(dict(turn_context.inline_context_metadata))
        manifest["inline_context_payload"] = str(len(inline_text))
        kwargs["context_manifest"] = manifest
        if turn_context.shortened_user_request:
            kwargs["shortened_user_request"] = turn_context.shortened_user_request

    if source_metadata:
        kwargs["source_metadata"] = source_metadata

    return kwargs


__all__ = [
    "TurnContext",
    "build_turn_context",
    "build_turn_context_for_agent",
    "estimate_turn_context_chars",
    "extract_inline_context_payload",
    "interpreter_session_context_paths",
    "large_context_threshold_chars",
    "load_large_context_rlm_kwargs",
    "resolve_effective_context_paths",
    "should_auto_route_large_context",
]
