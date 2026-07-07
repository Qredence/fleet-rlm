"""ExecutionBackend enum for selecting the runtime backend in stream_turn()."""

from __future__ import annotations

from enum import StrEnum


class ExecutionBackend(StrEnum):
    """Selects which execution backend ``stream_turn()`` dispatches to.

    Orthogonal to ``ExecutionMode`` (``auto`` / ``rlm_only`` / ``tools_only``):
    ``ExecutionBackend`` selects *which runtime*; ``ExecutionMode`` selects
    *how the legacy runtime behaves*. The two converge only in Phase 2B+.
    """

    legacy_agent_runtime = "legacy_agent_runtime"
    direct_rlm = "direct_rlm"


__all__ = ["ExecutionBackend"]
