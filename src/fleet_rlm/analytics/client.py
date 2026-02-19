"""PostHog client lifecycle utilities."""

from __future__ import annotations

from threading import Lock
from typing import Any
import logging

from posthog import Posthog

from .config import PostHogConfig

_LOGGER = logging.getLogger(__name__)
_CLIENT_LOCK = Lock()
_CLIENT: Posthog | None = None
_CLIENT_KEY: tuple[Any, ...] | None = None


def _client_identity(config: PostHogConfig) -> tuple[Any, ...]:
    return (
        config.api_key,
        config.host,
        config.flush_interval,
        config.flush_at,
    )


def get_posthog_client(config: PostHogConfig) -> Posthog | None:
    """Get or initialize a singleton PostHog client for this process."""
    if not config.enabled or not config.api_key:
        return None

    global _CLIENT, _CLIENT_KEY
    identity = _client_identity(config)

    with _CLIENT_LOCK:
        if _CLIENT is not None and _CLIENT_KEY == identity:
            return _CLIENT

        if _CLIENT is not None:
            try:
                _CLIENT.shutdown()
            except Exception as exc:
                # Best-effort shutdown; ignore errors to avoid impacting callers, but log for diagnostics.
                _LOGGER.warning("Failed to shutdown existing PostHog client", exc_info=exc)

        _CLIENT = Posthog(
            project_api_key=config.api_key,
            host=config.host,
            flush_interval=config.flush_interval,
            flush_at=config.flush_at,
            disable_geoip=True,
        )
        _CLIENT_KEY = identity
        return _CLIENT


def flush_posthog_client() -> None:
    """Flush pending events to PostHog."""
    with _CLIENT_LOCK:
        client = _CLIENT
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        return


def shutdown_posthog_client() -> None:
    """Shutdown the singleton client and release resources."""
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        client = _CLIENT
        _CLIENT = None
        _CLIENT_KEY = None

    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        return
