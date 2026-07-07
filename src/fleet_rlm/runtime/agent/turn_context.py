"""Per-turn context metadata for routing and dspy.RLM variable injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TurnContext:
    """Paths and estimates for one chat turn."""

    docs_path: str | None = None
    context_paths: list[str] = field(default_factory=list)
    repo_url: str | None = None
    repo_ref: str | None = None
    estimated_chars: int = 0
    threshold_chars: int = 0
    context_sources: list[str] = field(default_factory=list)
    inline_context_text: str = ""
    shortened_user_request: str | None = None
    inline_context_metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "docs_path": self.docs_path,
            "context_paths": list(self.context_paths),
            "repo_url": self.repo_url,
            "repo_ref": self.repo_ref,
            "estimated_chars": self.estimated_chars,
            "threshold_chars": self.threshold_chars,
            "context_sources": list(self.context_sources),
            "inline_context_text": self.inline_context_text,
            "shortened_user_request": self.shortened_user_request,
            "inline_context_metadata": dict(self.inline_context_metadata),
        }


__all__ = ["TurnContext"]
