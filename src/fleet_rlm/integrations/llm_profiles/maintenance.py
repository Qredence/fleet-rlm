"""One-shot repairs for persisted LLM provider profiles and role bindings.

The previous Google-specific model-id normalization branch is gone: Gemini now
folds into ``openai_chat_completion`` (it speaks OpenAI-compatible Chat
Completions via its ``/v1beta/openai/`` endpoint and users prefix with
``openai/`` themselves). What remains: dedup of imported-from-.env profiles,
plaintext-key re-encryption, and env mirroring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fleet_rlm.integrations.config.env_file import apply_env_updates, resolve_env_path

from .crypto import decrypt_api_key, encrypt_api_key
from .model_catalog import invalidate_profile_catalog
from .resolver import mirror_role_configs_to_env, resolve_active_role_configs
from .store import JsonLlmProfileStore, LlmProfileStore

logger = logging.getLogger(__name__)

IMPORT_PROFILE_LABEL = "Imported from .env"


def normalize_api_base(api_base: str) -> str:
    return api_base.strip().rstrip("/")


def normalize_binding_model_id(model_id: str) -> str:
    """Pass through — no provider-specific normalization now.

    Gemini id rewriting (``models/gemini-...`` -> bare) used to happen here;
    it is no longer needed because Gemini users supply ``openai/``-prefixed
    ids themselves against the OpenAI-compatible base, and the bare-id case
    is handled by ``custom_llm_provider`` at the resolver layer.
    """
    return model_id.strip()


def _repair_plaintext_api_keys(profiles: list[dict[str, Any]]) -> int:
    repaired = 0
    for profile in profiles:
        ciphertext = str(profile.get("api_key_ciphertext") or "")
        if not ciphertext:
            continue
        try:
            decrypt_api_key(ciphertext)
        except (UnicodeDecodeError, ValueError):
            if ciphertext.startswith(("sk-", "AIza", "dtn_", "phc_")):
                profile["api_key_ciphertext"] = encrypt_api_key(ciphertext)
                repaired += 1
    return repaired


@dataclass(slots=True)
class LlmProfileRepairReport:
    deduped_profiles: int = 0
    repaired_plaintext_keys: int = 0
    normalized_bindings: int = 0
    planner_reassigned: bool = False
    env_keys_updated: list[str] = field(default_factory=list)


def _repair_document(document: dict[str, Any]) -> LlmProfileRepairReport:
    report = LlmProfileRepairReport()
    profiles: list[dict[str, Any]] = list(document.get("profiles", []))
    bindings: list[dict[str, Any]] = list(document.get("role_bindings", []))
    referenced_profile_ids = {str(binding.get("profile_id")) for binding in bindings if binding.get("profile_id")}

    import_groups: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        if profile.get("name") != IMPORT_PROFILE_LABEL:
            continue
        key = normalize_api_base(str(profile.get("api_base") or ""))
        import_groups.setdefault(key, []).append(profile)

    remap: dict[str, str] = {}
    remove_ids: set[str] = set()
    for group in import_groups.values():
        if len(group) <= 1:
            continue
        canonical = next(
            (profile for profile in group if str(profile.get("id")) in referenced_profile_ids),
            max(group, key=lambda profile: str(profile.get("updated_at") or "")),
        )
        canonical_id = str(canonical.get("id"))
        for profile in group:
            profile_id = str(profile.get("id"))
            if profile_id == canonical_id:
                continue
            remap[profile_id] = canonical_id
            remove_ids.add(profile_id)
            report.deduped_profiles += 1

    if remap or remove_ids:
        profiles = [profile for profile in profiles if str(profile.get("id")) not in remove_ids]
        for binding in bindings:
            profile_id = binding.get("profile_id")
            if profile_id and str(profile_id) in remap:
                binding["profile_id"] = remap[str(profile_id)]

    profiles_by_id = {str(profile.get("id")): profile for profile in profiles}

    for binding in bindings:
        profile_id = binding.get("profile_id")
        if not profile_id:
            continue
        profile = profiles_by_id.get(str(profile_id))
        if profile is None:
            continue
        current_model = str(binding.get("model_id") or "")
        normalized_model = normalize_binding_model_id(current_model)
        if normalized_model != current_model:
            binding["model_id"] = normalized_model
            report.normalized_bindings += 1
            invalidate_profile_catalog(str(profile_id))

    report.repaired_plaintext_keys = _repair_plaintext_api_keys(profiles)

    referenced_profile_ids = {str(binding.get("profile_id")) for binding in bindings if binding.get("profile_id")}
    remaining_profiles: list[dict[str, Any]] = []
    for profile in profiles:
        profile_id = str(profile.get("id"))
        if profile.get("name") == IMPORT_PROFILE_LABEL and profile_id not in referenced_profile_ids:
            report.deduped_profiles += 1
            continue
        remaining_profiles.append(profile)

    document["profiles"] = remaining_profiles
    document["role_bindings"] = bindings
    return report


def repair_json_document(document: dict[str, Any]) -> LlmProfileRepairReport:
    return _repair_document(document)


async def repair_persisted_llm_profiles(
    store: LlmProfileStore,
    *,
    env_path: Path | None = None,
) -> LlmProfileRepairReport:
    """Repair duplicate imports, normalize binding model ids, and mirror to .env."""
    report = LlmProfileRepairReport()
    if not isinstance(store, JsonLlmProfileStore):
        logger.info("Skipping LLM profile repair for non-JSON profile store.")
        return report

    document = store._read_document()
    before_profiles = len(document.get("profiles", []))
    report = _repair_document(document)
    if (
        report.deduped_profiles
        or report.repaired_plaintext_keys
        or report.normalized_bindings
        or report.planner_reassigned
    ):
        store._write_document(document)
        logger.info(
            "Repaired LLM profiles: removed %d duplicate imports, normalized %d bindings, planner_reassigned=%s",
            report.deduped_profiles,
            report.normalized_bindings,
            report.planner_reassigned,
        )
    elif before_profiles:
        logger.debug("LLM profile repair found nothing to change.")

    resolved_env_path = env_path or resolve_env_path()
    try:
        role_configs = await resolve_active_role_configs(store)
        env_updates = mirror_role_configs_to_env(role_configs)
        if env_updates:
            apply_env_updates(updates=env_updates, env_path=resolved_env_path)
            report.env_keys_updated = sorted(env_updates)
            logger.info("Mirrored repaired LLM role bindings to env: %s", report.env_keys_updated)
    except Exception:
        logger.warning("Failed to mirror repaired LLM bindings to .env", exc_info=True)

    return report


async def repair_local_llm_profiles(
    *,
    profiles_path: Path | None = None,
    env_path: Path | None = None,
) -> LlmProfileRepairReport:
    store = JsonLlmProfileStore(path=profiles_path) if profiles_path else JsonLlmProfileStore()
    return await repair_persisted_llm_profiles(store, env_path=env_path)
