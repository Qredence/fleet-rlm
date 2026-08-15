"""PostHog analytics client for Fleet RLM.

Initialised once in the FastAPI lifespan and shared across the process.
The client is optional and policy-controlled: the selected ``config/fleet.toml``
profile decides whether analytics are enabled and which environment variable
holds the project token. A disabled or unconfigured client is a no-op so the
app always boots cleanly.

All events share one stable per-installation ``distinct_id`` persisted under
``<data_root>/analytics-instance-id``. The deterministic ``LocalScope.user_id``
is identical across installs and would collapse every deployment into one
PostHog user, so it is never used as the analytics identity.
"""

from __future__ import annotations

import atexit
import logging
import uuid
from pathlib import Path

from posthog import Posthog

from fleet_rlm.config import Settings

logger = logging.getLogger(__name__)

_client: Posthog | None = None
_distinct_id: str | None = None
_atexit_registered = False


def _load_or_create_instance_id(data_root: str) -> str:
    """Return the persistent per-installation analytics identity.

    Reads or writes a fresh ``uuid4`` under ``data_root``. The id is stable
    across restarts and unique per install; persistence failures never block
    startup and fall back to a process-random id.
    """
    path = Path(data_root) / "analytics-instance-id"
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        fresh = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
        return fresh
    except OSError:
        return str(uuid.uuid4())


def init_posthog(settings: Settings) -> None:
    """Initialise the singleton PostHog client from the selected TOML policy.

    Called once during the FastAPI lifespan startup. Re-initialisation is
    idempotent: a previous client is shut down first, and when the policy
    disables PostHog, or enables it without a resolvable project token, the
    module stays a no-op and startup never fails (fail-soft, mirroring the
    MLflow runtime).
    """
    global _client, _atexit_registered, _distinct_id

    if _client is not None:
        _client.shutdown()
        _client = None

    if not settings.posthog_enabled:
        logger.info("PostHog analytics disabled by the selected policy")
        return

    token = settings.posthog_project_token.get_secret_value() if settings.posthog_project_token is not None else None
    if not token:
        logger.error("PostHog: enabled by policy but no project token resolved — analytics are disabled")
        return

    _distinct_id = _load_or_create_instance_id(settings.data_root)
    _client = Posthog(
        token,
        host=settings.posthog_host or None,
        enable_exception_autocapture=False,
    )
    if not _atexit_registered:
        atexit.register(shutdown_posthog)
        _atexit_registered = True
    logger.info("PostHog analytics initialised (host=%s)", settings.posthog_host or "default")


def shutdown_posthog() -> None:
    """Flush and shut down the PostHog client.

    Called once during the FastAPI lifespan shutdown.
    """
    global _client
    if _client is not None:
        _client.shutdown()
        _client = None


def get_client() -> Posthog | None:
    """Return the live PostHog client, or *None* when analytics are disabled."""
    return _client


def get_distinct_id() -> str:
    """Return the stable per-installation analytics identity.

    All events must use this single identity so every install maps to exactly
    one PostHog user. Falls back to a process-random id when never initialised.
    """
    return _distinct_id or str(uuid.uuid4())
