"""Status diagnostics handler for bridge frontends."""

from __future__ import annotations

import os
from typing import Any

from fleet_rlm import runners
from fleet_rlm.utils.modal import get_default_volume_name, load_modal_config


def get_status(runtime: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a runtime status payload for UI health panels."""
    has_model = bool(os.environ.get("DSPY_LM_MODEL"))
    has_api_key = bool(
        os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    )
    planner_ready = has_model and has_api_key

    modal_cfg = load_modal_config()
    modal_from_env = bool(
        os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")
    )
    modal_from_profile = bool(
        modal_cfg.get("token_id") and modal_cfg.get("token_secret")
    )

    docs_loaded = 0
    active_alias = ""
    if runtime.agent is not None:
        docs = runtime.agent.list_documents()
        docs_loaded = len(docs.get("documents", []))
        active_alias = str(docs.get("active_alias", ""))

    try:
        secret_check = runners.check_secret_presence(secret_name=runtime.secret_name)
    except Exception as exc:  # pragma: no cover - runtime path
        secret_check = {"error": str(exc)}

    # Add guidance when credentials are missing
    guidance = []
    if not planner_ready:
        guidance.append(
            "Planner LLM not configured. Use /settings to set DSPY_LM_MODEL and DSPY_LLM_API_KEY."
        )
    if not modal_from_env and not modal_from_profile:
        guidance.append(
            "Modal credentials not found. Run 'modal setup' or use /settings to configure MODAL_TOKEN_ID and MODAL_TOKEN_SECRET."
        )
    if secret_check.get("present") is False:
        guidance.append(
            f"Modal secret '{runtime.secret_name}' not found. Create it with: modal secret create {runtime.secret_name} DSPY_LM_MODEL=... DSPY_LLM_API_KEY=..."
        )

    return {
        "session_id": runtime.session_id,
        "trace_mode": runtime.trace_mode,
        "planner_ready": planner_ready,
        "llm": {"model_set": has_model, "api_key_set": has_api_key},
        "modal": {
            "credentials_from_env": modal_from_env,
            "credentials_from_profile": modal_from_profile,
            "secret_name": runtime.secret_name,
            "configured_volume": runtime.volume_name,
            "workspace_default_volume": get_default_volume_name(),
        },
        "documents": {
            "loaded_count": docs_loaded,
            "active_alias": active_alias,
        },
        "permissions": dict(sorted(runtime.command_permissions.items())),
        "secret_check": secret_check,
        "guidance": guidance,
    }
