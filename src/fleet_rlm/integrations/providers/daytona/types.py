"""Consolidated Daytona type definitions.

Sections (in dependency order):
1. Serialization helpers
2. Sandbox spec and LM bootstrap types
3. Staged-context types
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Section 1: Serialization helpers
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_optional_text(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    if limit is not None and len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


# ---------------------------------------------------------------------------
# Section 2: Sandbox spec and LM bootstrap types
# ---------------------------------------------------------------------------


class DaytonaRunCancelled(RuntimeError):
    """Raised when a live Daytona rollout is cancelled by the caller."""


@dataclass(slots=True)
class SandboxSpec:
    """Declarative specification for Daytona sandbox creation.

    Wraps the Daytona SDK's ``Image`` declarative builder and
    ``CreateSandboxFrom*Params`` into a single portable object.

    The ``image`` field accepts a ``daytona.Image`` object built with
    the SDK's fluent API (``Image.debian_slim().pip_install()...``).
    When set, the sandbox is created with ``CreateSandboxFromImageParams``
    and Daytona caches the built image for 24 hours.

    When ``snapshot`` is set instead, the sandbox is created from a
    pre-built snapshot via ``CreateSandboxFromSnapshotParams``.

    When neither is set, a bare Python sandbox is created using the
    default Daytona snapshot.
    """

    language: str = "python"
    image: Any = None  # daytona.Image — kept as Any to avoid hard SDK import
    snapshot: str | None = None
    volume_name: str | None = None
    volume_mount_path: str | None = None
    volume_subpath: str | None = None
    env_vars: dict[str, str] | None = None
    labels: dict[str, str] | None = None
    ephemeral: bool = True
    auto_stop_interval: int | None = 1800  # 30 min; refresh_activity() resets
    auto_archive_interval: int | None = None
    auto_delete_interval: int | None = None
    cpu: int | None = None
    memory: int | None = None
    disk: int | None = None
    network_block_all: bool | None = None
    network_allow_list: str | None = None

    @property
    def uses_declarative_image(self) -> bool:
        """True when the spec carries a ``daytona.Image`` declarative builder."""
        return self.image is not None

    def _common_params(self, *, volume_id: str | None = None) -> dict[str, Any]:
        """Build shared keyword arguments for any SDK create-params constructor."""
        params: dict[str, Any] = {"language": self.language}
        if self.env_vars:
            params["env_vars"] = dict(self.env_vars)
        if self.labels:
            params["labels"] = dict(self.labels)
        if self.ephemeral is not None:
            params["ephemeral"] = self.ephemeral
        if self.auto_stop_interval is not None:
            params["auto_stop_interval"] = self.auto_stop_interval
        if self.auto_archive_interval is not None:
            params["auto_archive_interval"] = self.auto_archive_interval
        if self.auto_delete_interval is not None:
            params["auto_delete_interval"] = self.auto_delete_interval
        if self.snapshot and not self.image:
            params["snapshot"] = self.snapshot
        if self.cpu is not None or self.memory is not None or self.disk is not None:
            params["resources"] = {
                k: v
                for k, v in [
                    ("cpu", self.cpu),
                    ("memory", self.memory),
                    ("disk", self.disk),
                ]
                if v is not None
            }
        if self.network_block_all is not None:
            params["network_block_all"] = self.network_block_all
        if self.network_allow_list is not None:
            params["network_allow_list"] = self.network_allow_list
        if volume_id and self.volume_mount_path:
            mount_kwargs: dict[str, Any] = {
                "volume_id": volume_id,
                "mount_path": self.volume_mount_path,
            }
            if self.volume_subpath:
                mount_kwargs["subpath"] = self.volume_subpath
            params["volumes"] = [mount_kwargs]
        return params

    def to_create_params(self, *, volume_id: str | None = None) -> dict[str, Any]:
        """Build keyword arguments for the SDK create-params constructor.

        When ``image`` is set the returned dict includes an ``"image"`` key
        carrying the ``daytona.Image`` object (for ``CreateSandboxFromImageParams``).
        Otherwise the dict is suitable for ``CreateSandboxFromSnapshotParams``.
        """
        params = self._common_params(volume_id=volume_id)
        if self.image is not None:
            params["image"] = self.image
        return params


@dataclass(slots=True)
class SandboxLmRuntimeConfig:
    """Serializable LM bootstrap config passed into sandbox-local runtimes."""

    model: str
    api_key: str
    api_base: str | None = None
    max_tokens: int = 64_000
    delegate_model: str | None = None
    delegate_api_key: str | None = None
    delegate_api_base: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> SandboxLmRuntimeConfig:
        if not isinstance(raw, dict):
            raise ValueError("Sandbox LM config must be a dict.")
        model = _normalize_optional_text(raw.get("model"))
        api_key = _normalize_optional_text(raw.get("api_key"))
        if model is None or api_key is None:
            raise ValueError("Sandbox LM config requires model and api_key.")
        return cls(
            model=model,
            api_key=api_key,
            api_base=_normalize_optional_text(raw.get("api_base")),
            max_tokens=_coerce_positive_int(raw.get("max_tokens")) or 64_000,
            delegate_model=_normalize_optional_text(raw.get("delegate_model")),
            delegate_api_key=_normalize_optional_text(raw.get("delegate_api_key")),
            delegate_api_base=_normalize_optional_text(raw.get("delegate_api_base")),
        )


# ---------------------------------------------------------------------------
# Section 3: Staged-context types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextSource:
    """Host-sourced local context staged into the Daytona workspace."""

    source_id: str
    kind: str
    host_path: str
    staged_path: str
    source_type: str | None = None
    extraction_method: str | None = None
    file_count: int = 1
    skipped_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> ContextSource:
        if not isinstance(raw, dict):
            raise ValueError("Context source payload must be a dict.")
        source_id = _normalize_optional_text(raw.get("source_id"))
        kind = _normalize_optional_text(raw.get("kind"))
        host_path = _normalize_optional_text(raw.get("host_path"))
        staged_path = _normalize_optional_text(raw.get("staged_path"))
        if (
            source_id is None
            or kind is None
            or host_path is None
            or staged_path is None
        ):
            raise ValueError(
                "Context source requires source_id, kind, host_path, and staged_path."
            )
        return cls(
            source_id=source_id,
            kind=kind,
            host_path=host_path,
            staged_path=staged_path,
            source_type=_normalize_optional_text(raw.get("source_type")),
            extraction_method=_normalize_optional_text(raw.get("extraction_method")),
            file_count=_coerce_positive_int(raw.get("file_count")) or 1,
            skipped_count=_coerce_nonnegative_int(raw.get("skipped_count")) or 0,
            warnings=[
                str(item)
                for item in raw.get("warnings", []) or []
                if item is not None and str(item).strip()
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DaytonaSmokeResult:
    """Result of a Daytona live/runtime smoke check."""

    repo: str
    ref: str | None
    sandbox_id: str | None
    workspace_path: str = ""
    persisted_state_value: Any = None
    driver_started: bool = False
    finalization_mode: str = "unknown"
    termination_phase: str = "config"
    error_category: str | None = None
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    # Serialization helpers
    "_normalize_optional_text",
    "_coerce_positive_int",
    "_coerce_nonnegative_int",
    # Sandbox spec and LM types
    "DaytonaRunCancelled",
    "SandboxSpec",
    "SandboxLmRuntimeConfig",
    # Context types
    "ContextSource",
    # Smoke result
    "DaytonaSmokeResult",
]
