"""RLM routing resolution and URL document fetch helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.modules.context_routing import should_auto_route_large_context
from fleet_rlm.runtime.task_intent import extract_first_url, has_url_document_intent


@dataclass(slots=True)
class FetchedUrlDocument:
    """Fetched URL document payload passed into DSPy RLM as REPL variables."""

    source_url: str
    document_text: str = ""
    source_metadata: dict[str, str] = field(default_factory=dict)


def is_rlm_execution_mode(execution_mode: str) -> bool:
    return execution_mode in {"rlm", "rlm_only"}


def resolve_rlm_routing(
    *,
    execution_mode: str,
    user_request: str,
    force_escalate: bool,
    turn_context: TurnContext | None,
) -> tuple[bool, str, str | None]:
    should_auto_route_url = execution_mode == "auto" and has_url_document_intent(user_request)
    should_auto_route_large = should_auto_route_large_context(
        execution_mode=execution_mode,
        turn_context=turn_context,
    )
    if is_rlm_execution_mode(execution_mode) or force_escalate or should_auto_route_url or should_auto_route_large:
        if should_auto_route_url:
            return True, "url_document_rlm", extract_first_url(user_request)
        if should_auto_route_large:
            return True, "large_context_rlm", None
        return True, "forced_rlm", None
    return False, "auto", None


def fetch_url_document(*, interpreter: Any | None, source_url: str) -> FetchedUrlDocument:
    if interpreter is None:
        return FetchedUrlDocument(
            source_url=source_url,
            source_metadata={"status": "not_fetched", "reason": "interpreter_unavailable"},
        )
    try:
        from fleet_rlm.runtime.tools.document_tools import fetch_document_text

        fetched = fetch_document_text(source_url)
    except Exception as exc:
        return FetchedUrlDocument(
            source_url=source_url,
            source_metadata={"status": "error", "error": str(exc)},
        )

    if fetched.get("status") != "ok":
        return FetchedUrlDocument(
            source_url=source_url,
            source_metadata={
                "status": "error",
                "error": str(fetched.get("error", "unknown error")),
            },
        )

    text = str(fetched.get("text") or "")
    char_count = fetched.get("char_count", len(text))
    raw_metadata = fetched.get("metadata")
    metadata: dict[str, str] = {
        "status": "ok",
        "char_count": str(char_count),
    }
    if isinstance(raw_metadata, dict):
        metadata.update({str(key): str(value) for key, value in raw_metadata.items()})
    return FetchedUrlDocument(
        source_url=source_url,
        document_text=text,
        source_metadata=metadata,
    )


__all__ = [
    "FetchedUrlDocument",
    "fetch_url_document",
    "is_rlm_execution_mode",
    "resolve_rlm_routing",
]
