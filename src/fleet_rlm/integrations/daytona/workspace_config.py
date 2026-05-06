"""Immutable workspace configuration value object and reconfigure outcome enum."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from fleet_rlm.integrations.daytona.payload_models import _normalize_optional_text
from fleet_rlm.utils.paths import dedupe_paths


class WorkspaceConfig(BaseModel):
    """Immutable snapshot of workspace identity fields.

    Pydantic validates and normalizes external workspace inputs at the
    configuration boundary. The model is frozen so two configs can be compared
    with ``==`` to detect changes, and ``sandbox_labels`` is a frozenset so the
    whole object remains hashable.
    """

    model_config = ConfigDict(frozen=True)

    repo_url: str | None = None
    repo_ref: str | None = None
    context_paths: tuple[str, ...] = ()
    volume_name: str | None = None
    sandbox_labels: frozenset[tuple[str, str]] = frozenset()

    @field_validator("repo_url", "repo_ref", "volume_name", mode="before")
    @classmethod
    def _normalize_optional_text_field(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("context_paths", mode="before")
    @classmethod
    def _normalize_context_paths(cls, value: Any) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
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
        return tuple(dedupe_paths(normalized))

    @field_validator("sandbox_labels", mode="before")
    @classmethod
    def _normalize_sandbox_labels(cls, value: Any) -> frozenset[tuple[str, str]]:
        if value is None or value == "":
            return frozenset()
        raw_items = value.items() if isinstance(value, dict) else value
        try:
            items = list(raw_items)
        except TypeError:
            return frozenset()
        labels: list[tuple[str, str]] = []
        for item in items:
            try:
                key, label_value = item
            except (TypeError, ValueError):
                continue
            normalized_key = _normalize_optional_text(key)
            normalized_value = _normalize_optional_text(label_value)
            if normalized_key is not None and normalized_value is not None:
                labels.append((normalized_key, normalized_value))
        return frozenset(labels)

    @classmethod
    def from_kwargs(
        cls,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
    ) -> WorkspaceConfig:
        """Construct from raw (possibly unnormalized) kwargs, normalizing all fields."""
        return cls.model_validate(
            {
                "repo_url": repo_url,
                "repo_ref": repo_ref,
                "context_paths": context_paths,
                "volume_name": volume_name,
                "sandbox_labels": sandbox_labels,
            }
        )

    def as_dict_labels(self) -> dict[str, str]:
        """Return sandbox_labels as a plain dict for downstream APIs."""
        return dict(self.sandbox_labels)


class ReconfigureOutcome(StrEnum):
    """What happened when aconfigure_workspace / configure_workspace was called."""

    REUSED = "reused"
    UPDATED = "updated"
    RECREATED = "recreated"
    RESUMED = "resumed"
    CREATED = "created"


__all__ = ["ReconfigureOutcome", "WorkspaceConfig"]
