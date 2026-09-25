"""Live Daytona configuration checks before resource construction."""

from __future__ import annotations

from urllib.parse import urlsplit

from fleet_rlm.config.settings import Settings


class CompositionError(RuntimeError):
    """A live application cannot be constructed from the selected settings."""


_DATABRICKS_MLFLOW_CHAT_BASE_PATH = "/ai-gateway/mlflow/v1"


def _databricks_chat_base_url_is_valid(role: object) -> bool:
    """Return whether a Databricks Chat Completions role uses the gateway base."""
    model = getattr(role, "model", "")
    is_databricks_role = getattr(role, "api_key_env", None) == "DATABRICKS_TOKEN" or (
        isinstance(model, str) and model.lower().startswith("databricks-")
    )
    if not is_databricks_role:
        return True
    base_url = getattr(role, "base_url", None)
    if not isinstance(base_url, str):
        return False
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.path.rstrip("/") == _DATABRICKS_MLFLOW_CHAT_BASE_PATH
    )


def require_daytona_settings(settings: Settings) -> None:
    """Validate that all required Daytona runtime settings are configured."""
    if settings.run_environment != "daytona":
        raise CompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
    if not (settings.daytona_org_id or "").strip():
        missing.append("FLEET_DAYTONA_ORG_ID")
    if not (settings.daytona_snapshot or "").strip():
        missing.append("FLEET_DAYTONA_SNAPSHOT")
    if settings.rlm_recursion_enabled and not (settings.daytona_child_snapshot or "").strip():
        missing.append("FLEET_DAYTONA_CHILD_SNAPSHOT")
    from fleet_rlm.rlm.program import has_llm_credentials

    if not has_llm_credentials(settings):
        missing.append("configured provider API key")
    if any(not _databricks_chat_base_url_is_valid(role) for role in (settings.root_lm, settings.sub_lm)):
        missing.append("Databricks MLflow gateway base URL")
    if not (settings.database_url or "").strip():
        missing.append("FLEET_DATABASE_URL")
    if missing:
        raise CompositionError("Daytona composition missing required settings: " + ", ".join(missing))


__all__ = ["CompositionError", "require_daytona_settings"]
