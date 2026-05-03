"""Core Daytona transport types, payload models, and spec-building helpers."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

from fleet_rlm.utils.paths import dedupe_paths

# ---------------------------------------------------------------------------
# Payload normalization helpers (formerly payload_models.py)
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


# ---------------------------------------------------------------------------
# Pydantic payload models (formerly payload_models.py)
# ---------------------------------------------------------------------------


class SandboxLmRuntimeConfig(BaseModel):
    """Serializable LM bootstrap config passed into sandbox-local runtimes."""

    model: str
    api_key: str
    api_base: str | None = None
    max_tokens: int = 64_000
    delegate_model: str | None = None
    delegate_api_key: str | None = None
    delegate_api_base: str | None = None

    @field_validator("model", "api_key", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        text = _normalize_optional_text(value)
        if text is None:
            raise ValueError("Sandbox LM config requires model and api_key.")
        return text

    @field_validator(
        "api_base",
        "delegate_model",
        "delegate_api_key",
        "delegate_api_base",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text_fields(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("max_tokens", mode="before")
    @classmethod
    def _normalize_max_tokens(cls, value: Any) -> int:
        if value is None or value == "":
            return 64_000
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 64_000
        return parsed if parsed > 0 else 64_000

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_raw(cls, raw: Any) -> SandboxLmRuntimeConfig:
        if not isinstance(raw, dict):
            raise ValueError("Sandbox LM config must be a dict.")
        try:
            return cls.model_validate(raw)
        except Exception as exc:  # pragma: no cover - pydantic internals
            raise ValueError("Sandbox LM config requires model and api_key.") from exc


class ContextSource(BaseModel):
    """Host-sourced local context staged into the Daytona workspace."""

    source_id: str
    kind: str
    host_path: str
    staged_path: str
    source_type: str | None = None
    extraction_method: str | None = None
    file_count: int = 1
    skipped_count: int = 0
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_id", "kind", "host_path", "staged_path", mode="before")
    @classmethod
    def _normalize_required_fields(cls, value: Any) -> str:
        text = _normalize_optional_text(value)
        if text is None:
            raise ValueError(
                "Context source requires source_id, kind, host_path, and staged_path."
            )
        return text

    @field_validator("source_type", "extraction_method", mode="before")
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("file_count", mode="before")
    @classmethod
    def _normalize_file_count(cls, value: Any) -> int:
        if value is None or value == "":
            return 1
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 1
        return parsed if parsed > 0 else 1

    @field_validator("skipped_count", mode="before")
    @classmethod
    def _normalize_skipped_count(cls, value: Any) -> int:
        if value is None or value == "":
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed >= 0 else 0

    @field_validator("warnings", mode="before")
    @classmethod
    def _normalize_warnings(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, (str, bytes)):
            items = [value]
        else:
            try:
                items = list(value)
            except TypeError:
                items = [value]
        normalized: list[str] = []
        for item in items:
            text = _normalize_optional_text(item)
            if text is not None:
                normalized.append(text)
        return normalized

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_raw(cls, raw: Any) -> ContextSource:
        if not isinstance(raw, dict):
            raise ValueError("Context source payload must be a dict.")
        try:
            return cls.model_validate(raw)
        except Exception as exc:  # pragma: no cover - pydantic internals
            raise ValueError(
                "Context source requires source_id, kind, host_path, and staged_path."
            ) from exc


# ---------------------------------------------------------------------------
# State normalization helpers (formerly state_helpers.py)
# ---------------------------------------------------------------------------


def render_final_text(value: Any) -> str:
    """Extract and normalize final text from structured output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("final_markdown", "summary", "text", "content", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        nested_value = value.get("value")
        if nested_value is not value:
            nested_text = render_final_text(nested_value)
            if nested_text:
                return nested_text
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def history_messages(history: Any) -> list[dict[str, str]]:
    """Extract message list from a history object."""
    messages = getattr(history, "messages", [])
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    return []


def normalize_history_turn(raw: dict[str, Any]) -> dict[str, str] | None:
    """Normalize a single history turn into ``{user_request, assistant_response}``."""
    user_request = str(raw.get("user_request", "") or "").strip()
    assistant_response = render_final_text(raw.get("assistant_response", "")).strip()
    if not user_request and not assistant_response:
        return None
    return {
        "user_request": user_request,
        "assistant_response": assistant_response,
    }


def normalized_history_messages(history: Any) -> list[dict[str, str]]:
    """Return a clean list of normalized history turns."""
    normalized: list[dict[str, str]] = []
    for item in history_messages(history):
        turn = normalize_history_turn(item)
        if turn is not None:
            normalized.append(turn)
    return normalized


def normalized_context_sources(raw: Any) -> list[ContextSource]:
    """Normalize a raw sources list into validated ``ContextSource`` objects."""
    if not isinstance(raw, list):
        return []
    normalized: list[ContextSource] = []
    for item in raw:
        try:
            normalized.append(ContextSource.from_raw(item))
        except Exception:
            continue
    return normalized


# ---------------------------------------------------------------------------
# Exceptions and core types
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

    name: str | None = None
    language: str = "python"
    image: Any = None  # daytona.Image — kept as Any to avoid hard SDK import
    snapshot: str | None = None
    volume_name: str | None = None
    volume_mount_path: str | None = None
    volume_subpath: str | None = None
    env_vars: dict[str, str] | None = None
    labels: dict[str, str] | None = None
    ephemeral: bool = True
    auto_stop_interval: int | None = 30
    auto_archive_interval: int | None = 60
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
        if self.name:
            params["name"] = self.name
        if self.env_vars:
            params["env_vars"] = dict(self.env_vars)
        if self.labels:
            params["labels"] = dict(self.labels)
        if self.ephemeral is not None:
            params["ephemeral"] = self.ephemeral
        params.update(self._daytona_lifecycle_params())
        if self.snapshot and not self.image:
            params["snapshot"] = self.snapshot
        if self.cpu is not None or self.memory is not None or self.disk is not None:
            params["resources"] = {
                key: value
                for key, value in [
                    ("cpu", self.cpu),
                    ("memory", self.memory),
                    ("disk", self.disk),
                ]
                if value is not None
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

    def _daytona_lifecycle_params(self) -> dict[str, int]:
        """Return Daytona lifecycle settings in provider-minute units."""
        params: dict[str, int] = {}
        if self.auto_stop_interval is not None:
            params["auto_stop_interval"] = self.auto_stop_interval
        if self.auto_archive_interval is not None:
            params["auto_archive_interval"] = self.auto_archive_interval
        if self.auto_delete_interval is not None:
            params["auto_delete_interval"] = self.auto_delete_interval
        return params

    def to_create_params(self, *, volume_id: str | None = None) -> dict[str, Any]:
        """Build keyword arguments for the SDK create-params constructor."""
        params = self._common_params(volume_id=volume_id)
        if self.image is not None:
            params["image"] = self.image
        return params


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


# ---------------------------------------------------------------------------
# Sandbox spec-building helpers (formerly spec_runtime.py)
# ---------------------------------------------------------------------------

DEFAULT_SANDBOX_LABELS: dict[str, str] = {"managed-by": "fleet-rlm"}


def default_sandbox_name(*, now: datetime.datetime | None = None) -> str:
    """Return the dashboard-friendly default sandbox name."""
    timestamp = now or datetime.datetime.now(datetime.timezone.utc)
    return f"fleet-rlm-{timestamp:%Y%m%d-%H%M%S}"


def merge_sandbox_labels(
    *,
    default_labels: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge runtime-default labels with caller-provided labels."""
    merged = dict(default_labels or DEFAULT_SANDBOX_LABELS)
    if labels:
        merged.update(labels)
    return merged


def build_sandbox_spec(
    *,
    default_labels: Mapping[str, str] | None = None,
    name: str | None = None,
    volume_name: str | None = None,
    volume_subpath: str | None = None,
    image: Any = None,
    snapshot: str | None = None,
    env_vars: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
    cpu: int | None = None,
    memory: int | None = None,
    disk: int | None = None,
    auto_stop_interval: int | None = 30,
    auto_archive_interval: int | None = 60,
    auto_delete_interval: int | None = None,
    network_block_all: bool | None = None,
    network_allow_list: str | None = None,
) -> SandboxSpec:
    """Build a ``SandboxSpec`` with Daytona runtime defaults applied."""
    from .snapshot_runtime import resolve_default_snapshot
    from .volume_runtime import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH

    return SandboxSpec(
        name=name or default_sandbox_name(),
        language="python",
        image=image,
        snapshot=resolve_default_snapshot(image=image, snapshot=snapshot),
        volume_name=volume_name,
        volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        volume_subpath=volume_subpath,
        env_vars=dict(env_vars) if env_vars else None,
        labels=merge_sandbox_labels(default_labels=default_labels, labels=labels),
        ephemeral=True,
        auto_stop_interval=auto_stop_interval,
        auto_archive_interval=auto_archive_interval,
        auto_delete_interval=auto_delete_interval,
        cpu=cpu,
        memory=memory,
        disk=disk,
        network_block_all=network_block_all,
        network_allow_list=network_allow_list,
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
