"""Host-owned SkillCards, authorization, and progressive load seams."""

from fleet_rlm.skills.authorize import InvalidSkillSelectionError, SkillAuthorizer
from fleet_rlm.skills.capabilities import (
    CapabilityRegistry,
    CapabilityResolutionContext,
    CapabilityResolver,
    CapabilityRLMRequirements,
    TaskContract,
    TurnCapabilityBlueprint,
)
from fleet_rlm.skills.cards import to_card
from fleet_rlm.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillPathError,
    SkillValidationError,
)
from fleet_rlm.skills.loader import (
    bundled_skills_root,
    seed_bundled_skills,
    stable_skill_id,
)
from fleet_rlm.skills.models import (
    SkillCard,
    SkillRecord,
    SkillResource,
    SkillResourceDescriptor,
    SkillSelectionRef,
)
from fleet_rlm.skills.paths import normalize_skill_resource_path
from fleet_rlm.skills.ranking import rank_authorized_cards
from fleet_rlm.skills.registry import InMemorySkillRegistry
from fleet_rlm.skills.tools import (
    SkillToolHost,
    skill_activated_public_payload,
    skill_loaded_public_payload,
)

__all__ = [
    "InMemorySkillRegistry",
    "InvalidSkillSelectionError",
    "CapabilityRegistry",
    "CapabilityRLMRequirements",
    "CapabilityResolutionContext",
    "CapabilityResolver",
    "SkillAuthorizer",
    "SkillCard",
    "SkillError",
    "SkillNotFoundError",
    "SkillPathError",
    "SkillRecord",
    "SkillResource",
    "SkillResourceDescriptor",
    "SkillSelectionRef",
    "SkillToolHost",
    "SkillValidationError",
    "TaskContract",
    "TurnCapabilityBlueprint",
    "bundled_skills_root",
    "normalize_skill_resource_path",
    "rank_authorized_cards",
    "seed_bundled_skills",
    "skill_activated_public_payload",
    "skill_loaded_public_payload",
    "stable_skill_id",
    "to_card",
]
