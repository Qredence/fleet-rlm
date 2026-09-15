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
from collections.abc import Mapping
from pathlib import Path

from posthog import Posthog

from fleet_rlm.config.settings import Settings

logger = logging.getLogger(__name__)

_client: Posthog | None = None
_distinct_id: str | None = None
_atexit_registered = False


def _load_or_create_instance_id(data_root: str) -> str:
    """Return the persistent per-installation analytics identity.

    Reads or writes a fresh ``uuid4`` under ``data_root``. The id is stable
    across restarts and unique per install; persistence failures never block
    startup and fall back to a process-random id. A malformed identity file is
    replaced rather than re-read on every start, because a fresh random id per
    process would collapse the one-PostHog-user-per-install contract.
    """
    path = Path(data_root) / "analytics-instance-id"
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except (OSError, UnicodeDecodeError):
        logger.warning("PostHog analytics identity file is unreadable; issuing a fresh one")
    fresh = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
    except OSError:
        pass
    return fresh


def _shutdown_client(client: Posthog) -> None:
    """Shut one client down without letting an SDK failure escape."""
    try:
        client.shutdown()
    except Exception as exc:
        logger.warning("PostHog client shutdown failed (%s); continuing shutdown", type(exc).__name__)


def init_posthog(settings: Settings) -> None:
    """
    Initialize the singleton PostHog client according to the configured analytics policy.

    Called once during the FastAPI lifespan startup. Re-initialisation is idempotent: a previous
    client is shut down first. When the policy disables PostHog, or enables it without a
    resolvable project token, the module stays a no-op and startup never fails (fail-soft,
    mirroring the MLflow runtime).

    Parameters:
        settings (Settings): Application settings containing the analytics policy, project token, host, and data root.
    """
    global _client, _atexit_registered, _distinct_id

    if _client is not None:
        _shutdown_client(_client)
        _client = None

    if not settings.posthog_enabled:
        logger.info("PostHog analytics disabled by the selected policy")
        return

    token = settings.posthog_project_token.get_secret_value() if settings.posthog_project_token is not None else None
    if not token:
        logger.error("PostHog: enabled by policy but no project token resolved — analytics are disabled")
        return

    _distinct_id = _load_or_create_instance_id(settings.data_root)
    try:
        _client = Posthog(
            token,
            host=settings.posthog_host or None,
            enable_exception_autocapture=False,
        )
    except Exception as exc:
        # Analytics observe Fleet; they are never a startup dependency. Only the
        # exception type is logged: the SDK payload carries the project token.
        logger.warning("PostHog client construction failed (%s); analytics are disabled", type(exc).__name__)
        _client = None
        return
    if not _atexit_registered:
        atexit.register(shutdown_posthog)
        _atexit_registered = True
    logger.info("PostHog analytics initialised (host=%s)", settings.posthog_host or "default")


def shutdown_posthog() -> None:
    """Shut down the active PostHog client and clear the client reference.

    Called once during the FastAPI lifespan shutdown. A failing SDK shutdown
    must not fail application shutdown, so the failure is contained here.
    """
    global _client
    if _client is not None:
        _shutdown_client(_client)
        _client = None


def capture(event: str, *, properties: Mapping[str, object] | None = None) -> None:
    """Record one analytics event without ever failing the caller.

    Analytics observe Fleet behaviour; they never participate in it. A disabled
    client, a malformed installation identity, a transport error, or an SDK
    defect is contained here so telemetry can neither turn a durable success
    into a failed request nor strand an opened Turn owner awaiting cleanup.
    """
    client = _client
    if client is None:
        return
    try:
        client.capture(
            distinct_id=get_distinct_id(),
            event=event,
            properties=dict(properties or {}),
        )
    except Exception as exc:
        # Bounded on purpose: the capture payload carries the project token, so
        # the raw SDK failure is never logged.
        logger.warning("PostHog capture failed for event %s (%s); continuing", event, type(exc).__name__)


def get_client() -> Posthog | None:
    """Return the active PostHog analytics client.

    Routes must use :func:`capture` rather than this raw client: it exists for
    lifecycle introspection, not as a telemetry path.

    Returns:
        Posthog | None: The active client, or `None` when analytics are disabled.
    """
    return _client


def get_distinct_id() -> str:
    """
    Provide the analytics identity for the current installation.

    All events must use this single identity so every install maps to exactly one PostHog user.
    Returns the persistent installation identity, or a process-random identity if initialization
    has not occurred.
    """
    return _distinct_id or str(uuid.uuid4())
