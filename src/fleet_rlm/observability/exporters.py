"""Optional turn exporters — failures never affect chat."""

from __future__ import annotations

import logging
from typing import Protocol

from fleet_rlm.observability.record import TurnTrace

logger = logging.getLogger(__name__)


class TurnExporter(Protocol):
    def export(self, trace: TurnTrace) -> None: ...


class LoggingTurnExporter:
    """Best-effort structured log of a completed turn."""

    def export(self, trace: TurnTrace) -> None:
        logger.info(
            "turn_trace run_id=%s session_id=%s status=%s duration_ms=%s",
            trace.run_id,
            trace.session_id,
            trace.terminal_status,
            trace.duration_ms,
        )


class InMemoryTurnStore:
    """Test/dev store of recent traces."""

    def __init__(self, *, max_items: int = 256) -> None:
        self._max = max_items
        self.traces: list[TurnTrace] = []

    def export(self, trace: TurnTrace) -> None:
        self.traces.append(trace)
        if len(self.traces) > self._max:
            self.traces = self.traces[-self._max :]


def safe_export(exporter: TurnExporter | None, trace: TurnTrace) -> None:
    """Invoke exporter; swallow all failures."""
    if exporter is None:
        return
    try:
        exporter.export(trace)
    except Exception:  # noqa: BLE001 - observability must be non-fatal
        logger.exception("turn exporter failed (non-fatal)")
