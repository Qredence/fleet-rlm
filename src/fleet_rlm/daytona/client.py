"""Direct AsyncDaytona client construction and lifecycle management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daytona import AsyncDaytona

    from fleet_rlm.config.settings import Settings

_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"


def build_async_daytona_client(settings: Settings) -> AsyncDaytona:
    """Construct the asynchronous Daytona SDK client with configured credentials.

    Parameters:
        settings: Application settings containing Daytona API key and org ID.

    Returns:
        Configured AsyncDaytona client.
    """
    from daytona import AsyncDaytona, DaytonaConfig

    api_key: str | None = None
    if settings.daytona_api_key is not None:
        raw = settings.daytona_api_key
        api_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
        api_key = api_key or None

    config_kwargs: dict[str, Any] = {"api_url": _DAYTONA_CLOUD_API_URL}
    if api_key:
        config_kwargs["api_key"] = api_key
    if settings.daytona_org_id:
        config_kwargs["organization_id"] = settings.daytona_org_id

    config = DaytonaConfig(**config_kwargs) if config_kwargs else None
    client = AsyncDaytona(config)
    if settings.daytona_org_id and api_key and hasattr(client, "_api_client"):
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


build_daytona_client = build_async_daytona_client
