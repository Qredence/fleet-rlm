"""Short-lived WebSocket authentication tickets."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from .types import NormalizedIdentity


@dataclass(frozen=True)
class WebSocketTicket:
    """Identity bound to a one-time browser WebSocket ticket."""

    identity: NormalizedIdentity
    expires_at: float


@dataclass
class WebSocketTicketStore:
    """Process-local single-use ticket store for WebSocket handshakes."""

    ttl_seconds: int = 60
    _tickets: dict[str, WebSocketTicket] = field(default_factory=dict)

    def issue(self, identity: NormalizedIdentity) -> tuple[str, float]:
        self.prune()
        ticket = secrets.token_urlsafe(32)
        expires_at = time.time() + self.ttl_seconds
        self._tickets[ticket] = WebSocketTicket(identity=identity, expires_at=expires_at)
        return ticket, expires_at

    def consume(self, ticket: str) -> NormalizedIdentity | None:
        normalized = ticket.strip()
        if not normalized:
            return None
        record = self._tickets.pop(normalized, None)
        if record is None:
            return None
        if record.expires_at <= time.time():
            return None
        return record.identity

    def prune(self) -> None:
        now = time.time()
        expired = [ticket for ticket, record in self._tickets.items() if record.expires_at <= now]
        for ticket in expired:
            self._tickets.pop(ticket, None)
