"""Compatibility re-exports for Daytona payload, spec, and diagnostic types."""

from __future__ import annotations

from fleet_rlm.utils.paths import dedupe_paths

from .diagnostic_models import DaytonaRunCancelled, DaytonaSmokeResult
from .payload_models import (
    ContextSource,
    SandboxLmRuntimeConfig,
    history_messages,
    normalize_history_turn,
    normalized_context_sources,
    normalized_history_messages,
    render_final_text,
)
from .sandbox_spec import (
    DEFAULT_SANDBOX_LABELS,
    SandboxSpec,
    build_sandbox_spec,
    default_sandbox_name,
    merge_sandbox_labels,
)

__all__ = [
    "ContextSource",
    "DEFAULT_SANDBOX_LABELS",
    "DaytonaRunCancelled",
    "DaytonaSmokeResult",
    "SandboxLmRuntimeConfig",
    "SandboxSpec",
    "build_sandbox_spec",
    "dedupe_paths",
    "default_sandbox_name",
    "history_messages",
    "merge_sandbox_labels",
    "normalize_history_turn",
    "normalized_context_sources",
    "normalized_history_messages",
    "render_final_text",
]
