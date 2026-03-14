"""Typed websocket request normalization for runtime-specific options."""

from __future__ import annotations

from dataclasses import dataclass

from ...schemas import WSMessage


@dataclass(slots=True)
class DaytonaChatRequestOptions:
    """Normalized Daytona websocket options after schema validation."""

    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    batch_concurrency: int | None


def normalize_daytona_chat_request(
    msg: WSMessage,
) -> DaytonaChatRequestOptions | None:
    """Return a typed Daytona request payload when the turn targets Daytona."""

    if msg.runtime_mode != "daytona_pilot":
        return None

    repo_url = str(msg.repo_url or "").strip() or None
    repo_ref = str(msg.repo_ref or "").strip() or None
    context_paths = [
        str(item).strip() for item in (msg.context_paths or []) if str(item).strip()
    ]
    return DaytonaChatRequestOptions(
        repo_url=repo_url,
        repo_ref=repo_ref,
        context_paths=context_paths,
        batch_concurrency=msg.batch_concurrency,
    )
