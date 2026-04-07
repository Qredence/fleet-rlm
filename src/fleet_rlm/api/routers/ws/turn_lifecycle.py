"""Compatibility wrapper for websocket turn lifecycle services."""

from __future__ import annotations

from ...runtime_services.chat_persistence import initialize_turn_lifecycle

__all__ = ["initialize_turn_lifecycle"]
