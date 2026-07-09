"""Server install policy resolution for remote skill sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fleet_rlm.skills.schemas import SkillInstallPolicy

if TYPE_CHECKING:
    from fleet_rlm.api.config import AppConfig


def resolve_install_policy(cfg: AppConfig) -> SkillInstallPolicy:
    """Build install policy from server ``AppConfig`` env fields."""
    return SkillInstallPolicy(
        url_install_enabled=cfg.skill_remote_url_install_enabled,
        bundle_install_enabled=cfg.skill_remote_bundle_install_enabled,
        allowed_hosts=list(cfg.skill_remote_allowed_hosts),
        tap_url=cfg.skill_remote_tap_url,
    )


__all__ = ["resolve_install_policy"]
