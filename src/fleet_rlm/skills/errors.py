"""Typed skill catalog, visibility, validation, and resource access errors."""

from __future__ import annotations


class SkillError(ValueError):
    """Base error for skills package boundary failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SkillValidationError(SkillError):
    """Invalid skill name, metadata, or resource access preconditions."""

    def __init__(self, message: str, *, code: str = "invalid_skill_request") -> None:
        super().__init__(code, message)


class SkillNotFoundError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_found", f"Skill not found: {name}")


class SkillNotVisibleError(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__("skill_not_visible", f"Skill is not visible: {name}")


class SkillResourcePathError(SkillError):
    """Unsafe or disallowed skill-relative resource path."""

    def __init__(self, message: str, *, code: str = "invalid_resource_path") -> None:
        super().__init__(code, message)


class SkillResourceNotFoundError(SkillError):
    """Resource missing for an otherwise addressable visible skill."""

    def __init__(self) -> None:
        super().__init__("skill_resource_not_found", "Skill resource not found.")


class SkillScriptNotFoundError(SkillError):
    """Script missing or inaccessible for a selected skill."""

    def __init__(self) -> None:
        super().__init__("skill_script_not_found", "Skill script not found or inaccessible.")


class SkillScriptNotPermittedError(SkillError):
    """Trust or permission policy rejected script execution."""

    def __init__(self) -> None:
        super().__init__("skill_script_not_permitted", "Skill script execution is not permitted.")


class SkillWriteDeniedError(SkillError):
    """Write policy rejected the requested scope or actor."""

    def __init__(self, message: str = "Skill write is not permitted.", *, code: str = "skill_write_denied") -> None:
        super().__init__(code, message)


class SkillProtectedError(SkillError):
    """Target skill or scope is read-only and cannot be modified."""

    def __init__(
        self, message: str = "Skill is protected and cannot be modified.", *, code: str = "skill_protected"
    ) -> None:
        super().__init__(code, message)


class StagedChangeNotFoundError(SkillError):
    """Staged skill change id was not found or is no longer pending."""

    def __init__(self) -> None:
        super().__init__("staged_change_not_found", "Staged skill change not found.")


class SkillInstallDeniedError(SkillError):
    """Remote install policy rejected the request."""

    def __init__(
        self, message: str = "Remote skill install is not permitted.", *, code: str = "skill_install_denied"
    ) -> None:
        super().__init__(code, message)


class SkillInstallBlockedError(SkillError):
    """Security scan or validation blocked the install."""

    def __init__(
        self,
        message: str = "Skill install blocked by security policy.",
        *,
        code: str = "skill_install_blocked",
        scan_id: str | None = None,
    ) -> None:
        self.scan_id = scan_id
        super().__init__(code, message)


class SkillQuarantinedError(SkillError):
    """Install was quarantined for review."""

    def __init__(self, *, scan_id: str, message: str = "Skill install quarantined for review.") -> None:
        self.scan_id = scan_id
        super().__init__("skill_quarantined", message)


class SkillRemoteFetchError(SkillError):
    """Remote fetch failed or was rejected by SSRF policy."""

    def __init__(self, message: str = "Remote skill fetch failed.", *, code: str = "skill_remote_fetch_failed") -> None:
        super().__init__(code, message)


__all__ = [
    "SkillError",
    "SkillNotFoundError",
    "SkillNotVisibleError",
    "SkillProtectedError",
    "SkillResourceNotFoundError",
    "SkillResourcePathError",
    "SkillScriptNotFoundError",
    "SkillScriptNotPermittedError",
    "SkillValidationError",
    "SkillInstallBlockedError",
    "SkillInstallDeniedError",
    "SkillQuarantinedError",
    "SkillRemoteFetchError",
    "SkillWriteDeniedError",
    "StagedChangeNotFoundError",
]
