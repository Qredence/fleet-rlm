"""Evidence-sink protocol for runtime modules.

Runtime modules that persist cross-pass evidence declare what they need from a
storage backend via the :class:`EvidenceSink` protocol. Concrete backends
(Daytona, in-memory test fakes) live outside ``runtime/`` and are injected at
construction so business logic never imports its own adapters.
"""

from __future__ import annotations

from typing import Any, Protocol


class EvidenceSink(Protocol):
    """Structural surface for storing and listing cross-pass evidence.

    Implementations satisfy this protocol by shape — no inheritance required.
    See ``fleet_rlm.integrations.daytona.evidence_bridge.DaytonaEvidenceSink``
    for the production adapter.
    """

    def store(
        self,
        *,
        key: str,
        content: str,
        kind: str = "context",
        scope: str = "run",
        tags: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def list_items(self, *, scope: str = "run", limit: int = 50) -> dict[str, Any]: ...


__all__ = ["EvidenceSink"]
