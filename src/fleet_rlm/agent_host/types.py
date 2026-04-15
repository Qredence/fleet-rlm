"""Agent host protocols — define the narrow surface the host needs from api/."""

from __future__ import annotations

from typing import Any

from typing_extensions import Protocol


class SessionStoreProtocol(Protocol):
    """Minimal session storage surface needed by agent_host/."""

    sessions: dict[str, dict[str, Any]]
