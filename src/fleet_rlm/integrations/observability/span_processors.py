"""MLflow 3.12 span processors for fleet-rlm metadata enrichment.

Span processors are Callable[[LiveSpan], None] functions registered via
mlflow.tracing.configure(span_processors=[...]). They run once per span
at creation time.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def fleet_metadata_processor(
    *,
    app_env: str | None = None,
    workspace_id: str | None = None,
    version: str | None = None,
) -> Callable[[Any], None]:
    """Return a span processor that stamps workspace/env/version on every span."""
    resolved_app_env = app_env or os.getenv("APP_ENV", "local")
    resolved_workspace_id = workspace_id or os.getenv("WS_DEFAULT_WORKSPACE_ID", "default")
    resolved_version = version
    if resolved_version is None:
        try:
            from fleet_rlm import __version__

            resolved_version = __version__
        except Exception:
            resolved_version = "unknown"

    def _process(span: Any) -> None:
        try:
            span.set_attributes(
                {
                    "fleet_rlm.app_env": resolved_app_env,
                    "fleet_rlm.workspace_id": resolved_workspace_id,
                    "fleet_rlm.version": resolved_version,
                }
            )
        except Exception:
            logger.debug("Failed to enrich span with fleet metadata", exc_info=True)

    return _process


def build_span_processors(
    *,
    app_env: str | None = None,
    workspace_id: str | None = None,
) -> list[Callable[[Any], None]]:
    """Build the default fleet-rlm span processor chain."""
    return [fleet_metadata_processor(app_env=app_env, workspace_id=workspace_id)]
