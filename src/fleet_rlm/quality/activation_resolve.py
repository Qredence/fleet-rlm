"""Resolve workspace activations into fail-closed ActiveArtifact values."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fleet_rlm.runtime.active_artifacts import ActiveArtifact


def active_artifact_from_version_row(row: Any) -> ActiveArtifact | None:
    """Build an ActiveArtifact from a persistence artifact-version row.

    Returns None when the row is missing required fields so callers preserve
    literal code/catalog defaults (ADR-0006).
    """
    if row is None:
        return None
    target_kind = str(getattr(row, "target_kind", "") or "")
    if target_kind not in {"module", "skill"}:
        return None
    path_raw = getattr(row, "artifact_path", None)
    digest = getattr(row, "artifact_sha256", None)
    target_id = getattr(row, "target_id", None)
    if not path_raw or not digest or not target_id:
        return None
    path = Path(str(path_raw))
    if not path.is_file():
        return None
    return ActiveArtifact(
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=str(target_id),
        path=path,
        sha256=str(digest),
    )


async def resolve_workspace_active_artifact(
    persistence: Any,
    *,
    tenant_id: Any,
    workspace_id: Any,
    target_kind: Literal["module", "skill"],
    target_id: str,
    created_by_user_id: Any | None = None,
) -> ActiveArtifact | None:
    """Load the workspace activation pointer and return a verified path handle.

    Missing activation, unsupported local store, or unreadable payload yields
    None so module factories and Skill catalogs keep their default behavior.
    """
    get_activation = getattr(persistence, "get_target_activation", None)
    if not callable(get_activation):
        return None
    try:
        _activation, artifact_row = await get_activation(
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
        )
    except Exception:
        return None
    return active_artifact_from_version_row(artifact_row)


async def load_activated_skill_markdown_map(
    persistence: Any,
    *,
    tenant_id: Any,
    workspace_id: Any,
    skill_ids: list[str],
    created_by_user_id: Any | None = None,
) -> dict[str, str]:
    """Resolve activated Skill Markdown for selected skill ids (ownership seam).

    Skills without an activation pointer are omitted so the catalog default is
    preserved (ADR-0006 fail-closed).
    """
    from fleet_rlm.runtime.active_artifacts import resolve_skill_markdown

    overrides: dict[str, str] = {}
    for skill_id in skill_ids:
        artifact = await resolve_workspace_active_artifact(
            persistence,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            target_kind="skill",
            target_id=skill_id,
            created_by_user_id=created_by_user_id,
        )
        if artifact is None:
            continue
        try:
            overrides[skill_id] = resolve_skill_markdown("", artifact)
        except Exception:
            continue
    return overrides


async def load_workspace_skill_activation_map(
    persistence: Any,
    *,
    tenant_id: Any,
    workspace_id: Any,
    created_by_user_id: Any | None = None,
) -> dict[str, str]:
    """Preload all workspace Skill activations into a skill-id → Markdown map.

    Prefers ``list_target_activations`` (single workspace query). Falls back to
    an empty map when listing is unsupported or fails (ADR-0006 fail-closed).
    """
    from fleet_rlm.runtime.active_artifacts import resolve_skill_markdown

    list_fn = getattr(persistence, "list_target_activations", None)
    if not callable(list_fn):
        return {}
    try:
        rows = await list_fn(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            target_kind="skill",
            created_by_user_id=created_by_user_id,
        )
    except Exception:
        return {}

    overrides: dict[str, str] = {}
    for _activation, artifact_row in rows or []:
        artifact = active_artifact_from_version_row(artifact_row)
        if artifact is None:
            continue
        try:
            overrides[artifact.target_id] = resolve_skill_markdown("", artifact)
        except Exception:
            continue
    return overrides


def apply_activated_skill_markdown(agent: Any, mapping: dict[str, str] | None) -> None:
    """Attach activated Skill Markdown to AgentRuntime and its cognition module."""
    payload = dict(mapping or {})
    try:
        agent.activated_skill_markdown = payload
    except Exception:
        pass
    inner = getattr(agent, "agent", None)
    if inner is not None:
        try:
            inner.activated_skill_markdown = payload
        except Exception:
            pass


async def build_module_with_workspace_activation(
    slug: str,
    *,
    persistence: Any,
    tenant_id: Any,
    workspace_id: Any,
    created_by_user_id: Any | None = None,
) -> object | None:
    """Factory seam: build a managed module and apply an active artifact if any."""
    from fleet_rlm.quality.module_registry import build_module_with_optional_activation

    artifact = await resolve_workspace_active_artifact(
        persistence,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        target_kind="module",
        target_id=slug,
        created_by_user_id=created_by_user_id,
    )
    return build_module_with_optional_activation(slug, active_artifact=artifact)


__all__ = [
    "active_artifact_from_version_row",
    "apply_activated_skill_markdown",
    "build_module_with_workspace_activation",
    "load_activated_skill_markdown_map",
    "load_workspace_skill_activation_map",
    "resolve_workspace_active_artifact",
]
